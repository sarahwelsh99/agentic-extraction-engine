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
    """The generated class parses a row into the document's own columns."""
    get_cache().clear()
    response = _generate()

    assert response["status"] == "success", response
    code = response["generated_code"]["code"]
    assert response["generated_code"]["syntax_valid"]

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

    print("✓ test_generates_a_working_parser PASSED")


def test_short_row_does_not_raise():
    """Rows carrying fewer fields than the header are normal, not fatal."""
    response = _generate()
    namespace = {"FIELD_COUNT": 4}
    exec(response["generated_code"]["code"], namespace)

    result = namespace["DataExtractor"].parse_row(["10002"])
    assert isinstance(result, dict), "a short row must return a dict, not raise"
    # Short rows are padded to the declared width, not treated as broken
    assert len(result["values"]) == 4, result
    assert result["values"][0] == "10002"
    assert result["values"][3] is None

    print("✓ test_short_row_does_not_raise PASSED")


def test_format_spec_echoes_the_report():
    """The response reports the structure the parser was built for."""
    response = _generate()
    spec = response["generated_code"]["format_spec"]

    assert spec["delimiter"] == ","
    assert spec["source_format"] == "csv"
    assert spec["field_count"] == 4
    assert spec["header_row"] == 0

    print("✓ test_format_spec_echoes_the_report PASSED")


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


def test_rejected_document_is_skipped():
    """A document rejected upstream never reaches the model."""
    response = _generate(rejected=True, rejection_reason="No header row detected")

    assert response["status"] == "skipped"
    assert "header" in response["error"].lower()

    print("✓ test_rejected_document_is_skipped PASSED")


def test_missing_report_is_an_error():
    """Without a metadata report there is nothing to build a parser from."""
    response = _generate(report={})

    assert response["status"] == "error"
    assert "metadata_report" in response["error"]

    print("✓ test_missing_report_is_an_error PASSED")


def test_truncated_code_is_rejected():
    """Code whose parse_row never returns is refused, so it cannot be cached.

    Output that overruns max_tokens gets stripped by the repair loop until it
    compiles, which routinely removes the trailing return. Cached, that failure
    would be served for every document of this shape.
    """
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

    print("✓ test_truncated_code_is_rejected PASSED")


def test_hard_coded_width_is_rejected():
    """A literal width would be wrong for every other document sharing the parser.

    One cache entry now serves documents of any width, so the code must read
    FIELD_COUNT rather than embed a number.
    """
    tool = GenerateParserScriptTool()

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

    print("\u2713 test_hard_coded_width_is_rejected PASSED")


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

    print("\u2713 test_stub_before_the_real_class_is_skipped PASSED")


def test_prompt_is_bounded_by_the_header():
    """A very wide document must not produce an unbounded prompt."""
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

    print("✓ test_prompt_is_bounded_by_the_header PASSED")


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


def test_no_session_builds_the_full_document_prompt():
    """Without a session, every attempt gets the same from-scratch prompt."""
    tool = GenerateParserScriptTool()
    seen = {}

    def fake_call_vllm(prompt, max_tokens=2000):
        seen["prompt"] = prompt
        return "CODE"

    tool._call_vllm = fake_call_vllm
    code = tool._generate_code(REPORT, SAMPLE, None, session=None, max_tokens=2000)

    assert code == "CODE"
    assert seen["prompt"] == tool._build_prompt(REPORT, SAMPLE, None)

    print("✓ test_no_session_builds_the_full_document_prompt PASSED")


def test_empty_session_gets_the_full_document_prompt():
    """A session's first real generation still needs the whole document.

    Guards against the case where the session's first attempt was served
    from cache and never actually generated anything: the next attempt must
    not send a bare "fix this" with no code or document in view.
    """
    tool = GenerateParserScriptTool()
    session = FakeSession(replies=["CODE"])

    code = tool._generate_code(REPORT, SAMPLE, None, session=session, max_tokens=2000)

    assert code == "CODE"
    assert session.sent == [tool._build_prompt(REPORT, SAMPLE, None)]

    print("✓ test_empty_session_gets_the_full_document_prompt PASSED")


def test_session_with_history_gets_a_short_retry_prompt():
    """Once the session holds this tool's own prior turn, a retry is 'fix this'.

    The retry must reference the failure but must not rebuild the document
    structure prompt: that context already lives in the conversation.
    """
    tool = GenerateParserScriptTool()
    session = FakeSession(
        replies=["CODE-v2"],
        messages=[
            {"role": "user", "content": "<earlier full prompt>"},
            {"role": "assistant", "content": "CODE-v1"},
        ],
    )

    code = tool._generate_code(
        REPORT, SAMPLE, "parse_row raised IndexError", session=session, max_tokens=2000
    )

    assert code == "CODE-v2"
    assert len(session.sent) == 1
    retry_prompt = session.sent[0]
    assert "IndexError" in retry_prompt, retry_prompt
    assert REPORT["delimiter_name"] not in retry_prompt, "must not rebuild the document prompt"
    assert "Fix the DataExtractor class" in retry_prompt

    print("✓ test_session_with_history_gets_a_short_retry_prompt PASSED")


def test_call_session_retries_on_empty_extraction():
    """A reply with no extractable code is re-asked in the same conversation."""
    tool = GenerateParserScriptTool()
    session = FakeSession(replies=["not code at all", "CODE"])

    code = tool._call_session(session, "generate code", max_tokens=2000)

    assert code == "CODE"
    assert len(session.sent) == 2, "a bad generation must trigger a retry"

    print("✓ test_call_session_retries_on_empty_extraction PASSED")


def run_all_tests():
    tests = [
        test_generates_a_working_parser,
        test_short_row_does_not_raise,
        test_format_spec_echoes_the_report,
        test_cache_key_ignores_row_counts,
        test_rejected_document_is_skipped,
        test_missing_report_is_an_error,
        test_truncated_code_is_rejected,
        test_hard_coded_width_is_rejected,
        test_stub_before_the_real_class_is_skipped,
        test_prompt_is_bounded_by_the_header,
        test_no_session_builds_the_full_document_prompt,
        test_empty_session_gets_the_full_document_prompt,
        test_session_with_history_gets_a_short_retry_prompt,
        test_call_session_retries_on_empty_extraction,
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
