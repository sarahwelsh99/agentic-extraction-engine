"""Tool 3: Generate a parser script from Tool 2's metadata report using vLLM.

Uses the vLLM model (Qwen3-Coder-30B) running on localhost:8000 to generate a
Python class that parses one already-split row into its fields.

Input: metadata report from Tool 2 (delimiter, header position and width,
       sheet size) plus a sample of the document
Output: Python class exposing DataExtractor.parse_row
"""

import ast
import json
import re
import requests
import time
import logging
from typing import Any, Dict, List, Optional

from extraction.core import config
from extraction.schema_code_cache import get_cache

logger = logging.getLogger(__name__)


class GenerateParserScriptTool:
    """Generate parsing code from a document's structure using vLLM."""

    name = "generate_parser_script"
    description = "Generate a Python parser from a document structure report"

    def __init__(self):
        self.vllm_base = config.VLLM_API_BASE
        self.vllm_model = config.VLLM_MODEL
        self.timeout = config.VLLM_TIMEOUT
        self.max_retries = config.VLLM_MAX_RETRIES

    def __call__(self, inputs: Dict[str, Any]) -> str:
        """Generate parser script from schema using vLLM or cache.

        Checks schema code cache first. If schema has been seen before,
        returns cached code immediately. Otherwise calls vLLM and caches result.

        Args:
            inputs: {
                "guid": "document-guid",
                "raw_sample": "sampled records",
                "metadata_report": {...},  # From Tool 2: structure, not meaning
            }

        Returns:
            JSON string with generated code and metadata
        """
        try:
            guid = inputs.get("guid", "unknown")
            raw_sample = inputs.get("raw_sample", "")
            report = inputs.get("metadata_report") or {}
            # Set by Tool 5 when an earlier attempt failed: what went wrong, so
            # this attempt can avoid it.
            feedback = inputs.get("feedback")
            attempt = int(inputs.get("attempt", 1))
            # Optional LLMSession spanning this document's whole retry loop
            # (see run_pipeline.py). When given, a retry is a short follow-up
            # turn against the code and failure already in the conversation
            # rather than a prompt rebuilt from scratch each attempt.
            session = inputs.get("session")

            if not report:
                return json.dumps({
                    "status": "error",
                    "error": "Missing metadata_report in input",
                })

            if inputs.get("rejected"):
                return json.dumps({
                    "status": "skipped",
                    "guid": guid,
                    "error": inputs.get("rejection_reason")
                             or "Document rejected by structure detection",
                })

            # Key the cache on the structure that shapes the code: two documents
            # with the same delimiter and the same header parse identically.
            cache_key = self._cache_key(report)

            # ===== CHECK CACHE FIRST =====
            cache = get_cache()
            cache_hit = False
            generation_time = 0.0

            # A retry must not be served the script that just failed. The cache
            # is keyed on document shape, which has not changed, so a hit would
            # return the same broken code and the retry would be a no-op.
            generated_code = None if feedback else cache.get(cache_key)
            if generated_code:
                cache_hit = True
                logger.info(f"Cache HIT for guid {guid}")
            else:
                # Call vLLM to generate code - statefully, if a session was given
                start_time = time.time()
                generated_code = self._generate_code(
                    report, raw_sample, feedback, session,
                    max_tokens=self._output_budget(report),
                )
                generation_time = time.time() - start_time

                if not generated_code:
                    return json.dumps({
                        "status": "error",
                        "error": "Failed to generate code from vLLM",
                    })

                # Never cache code that cannot work: a cached bad parser is
                # served forever for every document sharing this schema.
                defect = self._validate_extractor(generated_code)
                if defect:
                    return json.dumps({
                        "status": "error",
                        "guid": guid,
                        "error": f"Generated code rejected: {defect}",
                    })

                # ===== STORE IN CACHE =====
                # A retry replaces the entry that failed, so the next document
                # of this shape gets the better script rather than the bad one.
                cache.set(cache_key, generated_code)
                cache.cleanup_if_needed()
                logger.info(f"Cache MISS → vLLM generated + cached for guid {guid}")

            # Validate generated code
            is_valid = self._validate_python_syntax(generated_code)

            return json.dumps({
                "status": "success",
                "guid": guid,
                "cache_hit": cache_hit,
                "attempt": attempt,
                "regenerated_after_failure": bool(feedback),
                "generation_time_sec": round(generation_time, 2),
                "generated_code": {
                    "language": "python",
                    "code": generated_code,
                    "format_spec": {
                        "source_format": report.get("format", "csv"),
                        "delimiter": report.get("delimiter", ","),
                        "encoding": report.get("encoding", "utf-8"),
                        "has_header": report.get("header_source") == "row",
                        "header_row": report.get("header_row_index", 0),
                        "field_count": report.get("header_field_count", 0),
                    },
                    "syntax_valid": is_valid,
                },
                "code_quality": {
                    "has_type_hints": "from typing import" in generated_code,
                    "has_error_handling": "except" in generated_code,
                    "has_validation": "validate" in generated_code.lower(),
                    "has_documentation": '"""' in generated_code or "'''" in generated_code,
                    "has_row_tracking": "_row" in generated_code or "row_num" in generated_code,
                    "generated_by": "cache" if cache_hit else "vLLM",
                },
            }, indent=2)

        except Exception as e:
            return json.dumps({
                "status": "error",
                "error": str(e),
            })

    # Bounds on the two interpolated values. Without them the prompt is
    # unbounded in both the schema block and the sample: on real documents it
    # reached 1.7M characters against a 65k-token context, which vLLM rejects.
    MAX_SAMPLE_LINES = 3
    MAX_SAMPLE_LINE_CHARS = 2000
    MAX_HEADER_NAMES = 120
    MAX_COLUMN_NAME_CHARS = 64
    MAX_FEEDBACK_CHARS = 600

    @staticmethod
    def _cache_key(report: Dict) -> Dict[str, Any]:
        """Structure that determines the generated code.

        Neither column names nor the column count belong here. The script reads
        the width from FIELD_COUNT at run time and never sees the names, so one
        parser serves every document of the same delimiter and raggedness
        whatever its schema or width. Measured on 49 real documents, including
        the width forked the cache into 35 entries; excluding it gives 4.
        """
        return {
            "delimiter": report.get("delimiter"),
            "has_header_row": report.get("header_source") == "row",
            "ragged": bool(report.get("ragged")),
        }

    def _generate_code(self, report: Dict, sample: str, feedback: Optional[str],
                        session, max_tokens: int) -> Optional[str]:
        """Get code from the model, statefully when a session is given.

        The first real generation in a session is the full document-structure
        prompt (feedback folded in the same way the stateless path always has,
        in case the session's first attempt was served from cache and never
        actually generated anything). Once the session holds a turn this tool
        itself generated, a retry is short: "fix this" against the code and
        failure already in the conversation, not a rebuilt prompt re-deriving
        the same structure from nothing.
        """
        if session is not None and session.messages:
            prompt = (
                "That attempt failed: "
                f"{(feedback or 'unknown error')[: self.MAX_FEEDBACK_CHARS]}\n\n"
                "Fix the DataExtractor class and return the complete corrected "
                "code. Output ONLY executable Python code - no explanations, "
                "no markdown fences."
            )
        else:
            prompt = self._build_prompt(report, sample, feedback)

        if session is not None:
            return self._call_session(session, prompt, max_tokens)
        return self._call_vllm(prompt, max_tokens=max_tokens)

    def _call_session(self, session, prompt: str, max_tokens: int) -> Optional[str]:
        """Send one turn through a stateful session.

        Same retry-on-transient-error behaviour as the stateless completions
        path (_call_vllm): a network hiccup gets a fresh attempt, a bad
        generation gets re-asked in the same conversation rather than treated
        as a network failure.
        """
        code = None
        for attempt in range(self.max_retries):
            try:
                text = session.send(prompt, temperature=0.3, max_tokens=max_tokens)
            except (TimeoutError, RuntimeError) as e:
                logger.error(f"LLM session error (attempt {attempt+1}/{self.max_retries}): {e}")
                if attempt >= self.max_retries - 1:
                    raise
                continue
            code = self._extract_code(text)
            if code:
                return code
        return code

    def _build_prompt(self, report: Dict, sample: str, feedback: str = None) -> str:
        """Prompt built from Tool 2's metadata report.

        The script returns values in column order and nothing else. Naming is a
        mechanical pairing of position to label, so the sandbox does it against
        the report; asking the model to transcribe the names lost five of
        thirty-seven columns on a real document, silently.

        Leaving names out also means the prompt no longer grows with the width
        of the document.
        """
        sample_str = "\n".join(
            line[: self.MAX_SAMPLE_LINE_CHARS]
            for line in sample.split("\n")[: self.MAX_SAMPLE_LINES]
        )

        delimiter = report.get("delimiter", ",")
        field_count = report.get("modal_field_count") or report.get("header_field_count")
        ragged = report.get("ragged")

        # From the Looker's structural_inspector, when it found tokens this
        # document uses to mean "no value" beyond a plain empty string (e.g.
        # "N/A", "-"). Purely additive: a report with no null_values produces
        # the same prompt as before.
        null_values = report.get("null_values") or []
        null_values_line = (
            f"- null tokens (treat as None, same as an empty field): {null_values!r}\n"
            if null_values else ""
        )

        # On a retry, say what went wrong before the constraints, so the failure
        # is context for the whole task rather than an afterthought.
        retry_block = (
            f"\nPREVIOUS ATTEMPT FAILED:\n{feedback[: self.MAX_FEEDBACK_CHARS]}\n"
            if feedback else ""
        )

        # The sample sits above the code marker: this is a completions endpoint
        # and the model continues from wherever the prompt stops, so a trailing
        # sample makes it emit more data rows instead of code.
        return f"""You are an automated code generation engine for delimited data extraction.

TASK:
Generate a Python class `DataExtractor` with a static method
`parse_row(row: List[str]) -> Dict[str, Any]` that returns:
  {{'values': [...], '_valid': bool, '_errors': [...]}}

'values' holds one entry per field of the row, in column order: at least
FIELD_COUNT entries, and more when the row carries more (see constraint 3).
Do NOT name the fields — the caller pairs positions with column names
afterwards.

FIELD_COUNT is a module-level integer the caller defines for you before your
code runs. USE THE NAME `FIELD_COUNT`; do NOT write the number in. This document
has {field_count} columns, but the same class is reused for documents of other
widths, so a hard-coded number would be wrong for all of them.

IMPORTANT: `row` is a single data row, already split on the delimiter. Read it
by index. Do not re-split it and do not look up column names in it.

DOCUMENT STRUCTURE:
- delimiter: {delimiter!r} ({report.get('delimiter_name', 'unknown')})
- quote character: {report.get('quote_char', '"')!r}
- columns per row: {field_count}  (available to your code as FIELD_COUNT)
- field count observed: modal {report.get('modal_field_count')}, \
range {report.get('min_field_count')}-{report.get('max_field_count')}\
{' (ragged: rows vary in width)' if ragged else ''}
- rows in sheet: {report.get('sheet_record_count')}
- sheet size in bytes: {report.get('sheet_byte_length')}
{null_values_line}
INPUT SAMPLE:
{sample_str}
{retry_block}
CONSTRAINTS:
1. Output ONLY executable Python code. No explanations, introductions, or markdown.
2. Use @staticmethod decorator. Include type hints from typing module.
3. Rows vary in width and NEITHER DIRECTION IS AN ERROR:
   - A SHORT row (fewer than FIELD_COUNT fields) is normal, because trailing
     empty cells get trimmed. Pad 'values' with None up to FIELD_COUNT.
   - A LONG row (more than FIELD_COUNT fields) is also normal. Return every
     field it has, so 'values' is longer than FIELD_COUNT. Do NOT drop the
     extra fields and do NOT report them as a problem.
   Never raise, and never set '_valid' False, because a row is a different
   length from the header.
4. Strip surrounding whitespace. An empty field becomes None. Every other
   value is kept as a string exactly as it appears. If null tokens are listed
   above, a field matching one of them (after stripping) also becomes None -
   this is recognizing the document's own null convention, not inferring a
   type.
5. Do NOT infer types and do NOT validate content. No field is a number, a
   date or a boolean here; there is no schema to enforce. Inventing rules
   rejects perfectly good values such as 'Yes' or a URL.
6. Set '_valid' False and append to '_errors' only if the row itself cannot be
   read at all, never because a value is missing, empty or unexpected.

Write the complete class, starting with the imports.

CODE:
"""

    @staticmethod
    def _output_budget(report: Dict) -> int:
        """Token allowance for the generated class.

        Scaled to the number of columns: each costs roughly a guarded read. A
        fixed 2000 truncated wide extractors mid-function, and the repair loop
        then stripped the trailing return.
        """
        # The class is a loop over the row and a pad to FIELD_COUNT. It neither
        # restates a key per column nor mentions the width, so its size does not
        # depend on the document at all.
        return 2000

    def _call_vllm(self, prompt: str, max_tokens: int = 2000) -> Optional[str]:
        """Call vLLM to generate code using completions endpoint.

        Uses completions endpoint with code cleaning to extract Python code
        from vLLM response, filtering out markdown and explanation text.

        Args:
            prompt: Prompt for code generation

        Returns:
            Generated Python code or None if failed
        """
        url = f"{self.vllm_base}/v1/completions"

        payload = {
            "model": self.vllm_model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "top_p": 0.95,
        }

        logger.debug(f"vLLM request: {url}, model={self.vllm_model}, prompt_len={len(prompt)}")

        try:
            for attempt in range(self.max_retries):
                try:
                    response = requests.post(
                        url,
                        json=payload,
                        timeout=self.timeout,
                    )
                    response.raise_for_status()

                    result = response.json()
                    if "choices" in result and len(result["choices"]) > 0:
                        text = result["choices"][0].get("text", "").strip()

                        code = self._extract_code(text)
                        if code:
                            return code

                except requests.exceptions.Timeout:
                    if attempt < self.max_retries - 1:
                        continue
                    raise
                except requests.exceptions.RequestException as e:
                    logger.error(f"vLLM request error (attempt {attempt+1}/{self.max_retries}): {e}")
                    if hasattr(e, 'response') and e.response is not None:
                        logger.error(f"  Response status: {e.response.status_code}")
                        logger.error(f"  Response text: {e.response.text[:500]}")
                    if attempt < self.max_retries - 1:
                        continue
                    raise

        except Exception as e:
            logger.error(f"vLLM error: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None

        return None

    def _extract_code(self, text: str) -> Optional[str]:
        """Pull the usable class out of the model's reply.

        The reply is not always one clean block. The model sometimes emits a
        stub first — `def parse_row(...): pass` with a "# Implement this method"
        comment — and the real implementation after it. Taking the first class
        definition therefore yielded a parser that returns nothing, which read
        as a truncated generation and cost a retry.

        So every plausible starting point is tried, latest first, and the first
        candidate that is a working extractor wins. Trailing prose is trimmed by
        removing lines from the end until the code compiles.

        Args:
            text: Raw completion from the model

        Returns:
            Usable code, or None if no candidate validates
        """
        body = text.strip()
        for fence in ("```python", "```"):
            if body.startswith(fence):
                body = body[len(fence):]
        if body.endswith("```"):
            body = body[:-3]

        lines = body.split("\n")

        # De-duplicated: the reply often repeats its imports above each class,
        # and prepending them all left the same line in the output twice.
        imports, seen = [], set()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and stripped not in seen:
                seen.add(stripped)
                imports.append(stripped)
        class_starts = [
            i for i, line in enumerate(lines) if line.strip().startswith("class ")
        ]
        if not class_starts:
            return self._trim_to_valid(body)

        # Latest class first: a stub precedes the real implementation, never
        # follows it. Imports are carried along regardless of where they sat,
        # because a candidate starting at `class` would otherwise compile and
        # validate while failing at run time on an undefined name.
        for start in reversed(class_starts):
            body_lines = [
                line for line in lines[start:]
                if not line.strip().startswith(("import ", "from "))
            ]
            candidate = self._trim_to_valid("\n".join(imports + [""] + body_lines))
            if candidate and self._validate_extractor(candidate) is None:
                return candidate

        # Nothing validated: return the first class so the caller can report a
        # specific defect rather than "no code at all"
        return self._trim_to_valid("\n".join(
            imports + [""] + [
                line for line in lines[class_starts[0]:]
                if not line.strip().startswith(("import ", "from "))
            ]))

    @staticmethod
    def _trim_to_valid(code: str) -> Optional[str]:
        """Drop trailing lines until the code compiles, or give up."""
        block = code.strip()
        while block:
            try:
                compile(block, "<string>", "exec")
                return block
            except SyntaxError:
                lines = block.split("\n")
                if len(lines) <= 1:
                    return None
                block = "\n".join(lines[:-1]).strip()
        return None

    def _validate_extractor(self, code: str) -> Optional[str]:
        """Check the code is a usable extractor before it is trusted or cached.

        Syntax validity is not enough. When the model overruns max_tokens the
        repair loop strips lines until the remainder compiles, which routinely
        removes the trailing `return`; parse_row then returns None for every
        row and the document scores zero. Cached, that failure is permanent.

        Uses AST inspection rather than executing the code: this runs outside
        the sandbox, and the sandbox is the only place model-written code
        should run.

        Returns:
            None if usable, else a description of what is wrong
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return f"syntax error: {exc}"

        extractor = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.ClassDef) and n.name == "DataExtractor"),
            None,
        )
        if extractor is None:
            return "no DataExtractor class"

        parse_row = next(
            (n for n in extractor.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "parse_row"),
            None,
        )
        if parse_row is None:
            return "DataExtractor has no parse_row method"

        # A bare `return` is as useless here as none at all
        returns_value = any(
            isinstance(n, ast.Return) and n.value is not None
            for n in ast.walk(parse_row)
        )
        if not returns_value:
            return "parse_row never returns a value (likely truncated output)"

        # The width must be read, not written in. One cache entry now serves
        # documents of every width, so a literal here is wrong for all but one
        # of them — and would be served from cache to all of them.
        uses_field_count = any(
            isinstance(n, ast.Name) and n.id == "FIELD_COUNT"
            for n in ast.walk(tree)
        )
        if not uses_field_count:
            return (
                "parse_row does not reference FIELD_COUNT; it appears to "
                "hard-code the column count, which is wrong for every other "
                "document sharing this parser"
            )

        return None

    def _validate_python_syntax(self, code: str) -> bool:
        """Validate Python code syntax.

        Args:
            code: Python code to validate

        Returns:
            True if syntax is valid, False otherwise
        """
        try:
            compile(code, "<string>", "exec")
            return True
        except SyntaxError:
            return False
