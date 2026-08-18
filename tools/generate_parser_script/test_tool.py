"""Tests for generate_parser_script (Tool 3).

Tool 3 turns Tool 2's metadata report into a parser. The report describes
structure only, so these tests assert on parsing behaviour, never on meaning.
"""

import json
from tools.generate_parser_script.tool import GenerateParserScriptTool
from extraction.schema_code_cache import get_cache


REPORT = {
    "delimiter": ",",
    "delimiter_name": "comma",
    "format": "csv",
    "quote_char": '"',
    "encoding": "utf-8",
    "header_row_index": 0,
    "header_source": "row",
    "header_field_count": 4,
    "header_char_length": 38,
    "header_names": ["employee_id", "first_name", "last_name", "email"],
    "header_cells_that_look_like_data": [],
    "sheet_record_count": 1200,
    "sheet_byte_length": 48000,
    "data_row_count": 2,
    "modal_field_count": 4,
    "min_field_count": 4,
    "max_field_count": 4,
    "ragged": False,
}
SAMPLE = (
    "employee_id,first_name,last_name,email\n"
    "10001,John,Smith,john@company.com\n"
    "10002,Jane,Doe,jane@company.com"
)


def _generate(report=None, sample=SAMPLE, **extra):
    tool = GenerateParserScriptTool()
    return json.loads(tool({
        "guid": "test-guid",
        "raw_sample": sample,
        "metadata_report": report if report is not None else REPORT,
        **extra,
    }))


def test_generates_a_working_parser():
    """The generated class parses a row into the document's own columns,
    pads a short row rather than raising, and echoes the structure it was
    built for."""
    get_cache().clear()
    response = _generate()

    assert response["status"] == "success", response
    code = response["generated_code"]["code"]
    assert response["generated_code"]["syntax_valid"]

    spec = response["generated_code"]["format_spec"]
    assert spec["delimiter"] == ","
    assert spec["source_format"] == "csv"
    assert spec["field_count"] == 4
    assert spec["header_row"] == 0

    # FIELD_COUNT is supplied by the sandbox at run time, not written into the
    # code: that is what lets one cached parser serve documents of any width.
    namespace = {"FIELD_COUNT": 4}
    exec(code, namespace)
    extractor = namespace.get("DataExtractor")
    assert extractor is not None, "code must define DataExtractor"

    # The script returns values in column order and never names them: naming
    # is the sandbox's job, so a column cannot be dropped or renamed here.
    parsed = extractor.parse_row(["10001", "John", "Smith", "john@company.com"])
    assert isinstance(parsed, dict), parsed
    values = parsed["values"]
    assert len(values) == 4, values
    assert values[3] == "john@company.com", values
    assert parsed.get("_valid") is True, parsed

    # Rows carrying fewer fields than the header are normal, not fatal.
    short = extractor.parse_row(["10002"])
    assert len(short["values"]) == 4, short
    assert short["values"][0] == "10002"
    assert short["values"][3] is None

    print("✓ test_generates_a_working_parser PASSED")


def test_error_paths_before_generation():
    """A document rejected upstream never reaches the model; a missing report
    is refused outright - neither needs a live generation."""
    rejected = _generate(rejected=True, rejection_reason="No header row detected")
    assert rejected["status"] == "skipped"
    assert "header" in rejected["error"].lower()

    missing_report = _generate(report={})
    assert missing_report["status"] == "error"
    assert "metadata_report" in missing_report["error"]

    print("✓ test_error_paths_before_generation PASSED")


def test_cache_key_ignores_row_counts():
    """Two documents of different size but identical shape share generated code.

    Row counts and byte sizes change the data, not the parser, so including
    them would miss cache hits across every document of the same layout.
    """
    tool = GenerateParserScriptTool()
    bigger = {**REPORT, "sheet_record_count": 999999, "data_row_count": 4000}
    assert tool._cache_key(REPORT) == tool._cache_key(bigger)

    # Column names no longer shape the code, so they no longer split the cache
    renamed = {**REPORT, "header_names": ["a", "b", "c", "d"]}
    assert tool._cache_key(REPORT) == tool._cache_key(renamed)

    # A different delimiter is a different parser
    other = {**REPORT, "delimiter": "|"}
    assert tool._cache_key(REPORT) != tool._cache_key(other)

    print("✓ test_cache_key_ignores_row_counts PASSED")


def test_bad_generated_code_is_rejected():
    """Code that never returns, and code with a hard-coded width, are both
    refused before they can be cached."""
    tool = GenerateParserScriptTool()

    truncated = (
        "from typing import Dict, Any, List\n"
        "class DataExtractor:\n"
        "    @staticmethod\n"
        "    def parse_row(row: List[str]) -> Dict[str, Any]:\n"
        "        values = [None] * FIELD_COUNT\n"
    )
    assert tool._validate_extractor(truncated) is not None
    complete = truncated + "        return {'values': values}\n"
    assert tool._validate_extractor(complete) is None
    assert "DataExtractor" in tool._validate_extractor("x = 1")

    hard_coded = (
        "from typing import Dict, Any, List\n"
        "class DataExtractor:\n"
        "    @staticmethod\n"
        "    def parse_row(row: List[str]) -> Dict[str, Any]:\n"
        "        values = list(row) + [None] * (21 - len(row))\n"
        "        return {'values': values}\n"
    )
    defect = tool._validate_extractor(hard_coded)
    assert defect is not None and "FIELD_COUNT" in defect, defect

    print("✓ test_bad_generated_code_is_rejected PASSED")


def test_stub_before_the_real_class_is_skipped():
    """The model sometimes emits a stub first; the implementation comes second.

    Taking the first class definition yielded a parser returning nothing, which
    read as a truncated generation and cost a needless retry.
    """
    tool = GenerateParserScriptTool()

    reply = (
        "```python\n"
        "from typing import List, Dict, Any\n"
        "\n"
        "class DataExtractor:\n"
        "    @staticmethod\n"
        "    def parse_row(row: List[str]) -> Dict[str, Any]:\n"
        "        # Implement this method\n"
        "        pass\n"
        "\n"
        "class DataExtractor:\n"
        "    @staticmethod\n"
        "    def parse_row(row: List[str]) -> Dict[str, Any]:\n"
        "        values = [f.strip() or None for f in row]\n"
        "        while len(values) < FIELD_COUNT:\n"
        "            values.append(None)\n"
        "        return {'values': values, '_valid': True, '_errors': []}\n"
        "```\n"
        "That should handle your data.\n"
    )
    code = tool._extract_code(reply)

    assert code is not None
    assert tool._validate_extractor(code) is None, tool._validate_extractor(code)
    assert "pass" not in code, "the stub must not be what is returned"
    assert "That should handle" not in code, "trailing prose must be trimmed"
    # imports must survive even though the chosen class came later
    assert "from typing import" in code, code

    namespace = {"FIELD_COUNT": 3}
    exec(code, namespace)
    assert namespace["DataExtractor"].parse_row(["a"])["values"] == ["a", None, None]

    print("✓ test_stub_before_the_real_class_is_skipped PASSED")


def test_prompt_is_bounded_and_carries_null_values():
    """A very wide document must not produce an unbounded prompt, and
    null_values from the Looker (when present) show up as extra prompt
    context without changing the prompt for a report that lacks them."""
    tool = GenerateParserScriptTool()
    wide = {
        **REPORT,
        "header_names": [f"column_{i}" for i in range(5000)],
        "header_field_count": 5000,
        "modal_field_count": 5000,
    }
    prompt = tool._build_prompt(wide, SAMPLE)

    # Names are no longer sent at all, so width costs the prompt nothing
    narrow = tool._build_prompt(REPORT, SAMPLE)
    assert len(prompt) < 20000, len(prompt)
    assert abs(len(prompt) - len(narrow)) < 200, (len(prompt), len(narrow))

    with_nulls = tool._build_prompt({**REPORT, "null_values": ["N/A", "-"]}, SAMPLE)
    assert "N/A" in with_nulls and "-" in with_nulls
    assert len(with_nulls) > len(narrow)

    print("✓ test_prompt_is_bounded_and_carries_null_values PASSED")


class FakeSession:
    """Stands in for LLMSession: records prompts, replies are scripted."""

    def __init__(self, replies, messages=None):
        self.replies = list(replies)
        self.messages = list(messages) if messages else []
        self.sent = []

    def send(self, prompt, temperature=0.0, max_tokens=2000):
        self.sent.append(prompt)
        reply = self.replies[len(self.sent) - 1]
        self.messages.append({"role": "user", "content": prompt})
        self.messages.append({"role": "assistant", "content": reply})
        return reply


def test_session_behavior():
    """Three branches of _generate_code's session handling, plus the retry
    a bad extraction triggers within one session:

    - no session: every attempt gets the same from-scratch prompt.
    - an empty session (first attempt was served from cache and never
      actually generated anything): still needs the whole document prompt.
    - a session already holding this tool's own prior turn: a retry is a
      short "fix this", not a rebuilt document-structure prompt.
    - a reply with no extractable code is re-asked in the same conversation.
    """
    tool = GenerateParserScriptTool()

    seen = {}

    def fake_call_vllm(prompt, max_tokens=2000):
        seen["prompt"] = prompt
        return "CODE"

    tool._call_vllm = fake_call_vllm
    code = tool._generate_code(REPORT, SAMPLE, None, session=None, max_tokens=2000)
    assert code == "CODE"
    assert seen["prompt"] == tool._build_prompt(REPORT, SAMPLE, None)

    empty_session = FakeSession(replies=["CODE"])
    code = tool._generate_code(REPORT, SAMPLE, None, session=empty_session, max_tokens=2000)
    assert code == "CODE"
    assert empty_session.sent == [tool._build_prompt(REPORT, SAMPLE, None)]

    session_with_history = FakeSession(
        replies=["CODE-v2"],
        messages=[
            {"role": "user", "content": "<earlier full prompt>"},
            {"role": "assistant", "content": "CODE-v1"},
        ],
    )
    code = tool._generate_code(
        REPORT, SAMPLE, "parse_row raised IndexError",
        session=session_with_history, max_tokens=2000,
    )
    assert code == "CODE-v2"
    assert len(session_with_history.sent) == 1
    retry_prompt = session_with_history.sent[0]
    assert "IndexError" in retry_prompt, retry_prompt
    assert REPORT["delimiter_name"] not in retry_prompt, "must not rebuild the document prompt"
    assert "Fix the DataExtractor class" in retry_prompt

    retrying_session = FakeSession(replies=["not code at all", "CODE"])
    code = tool._call_session(retrying_session, "generate code", max_tokens=2000)
    assert code == "CODE"
    assert len(retrying_session.sent) == 2, "a bad generation must trigger a retry"

    print("✓ test_session_behavior PASSED")


def run_all_tests():
    tests = [
        test_generates_a_working_parser,
        test_error_paths_before_generation,
        test_cache_key_ignores_row_counts,
        test_bad_generated_code_is_rejected,
        test_stub_before_the_real_class_is_skipped,
        test_prompt_is_bounded_and_carries_null_values,
        test_session_behavior,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Test Results: {passed}/{len(tests)} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_all_tests() else 1)
