"""Tests for the pipeline state machine (extraction/core/pipeline_agent.py).

Looker runs once, then Thinker -> Tester -> Eval loop with feedback until Eval
passes or the retry ceiling is hit. Every tool is faked here (plain callables,
same input-dict-to-JSON-string convention every real tool follows) so these
pin the state machine's own transitions - not vLLM, not Docker.
"""

import json
from extraction.core import config
from extraction.core.pipeline_agent import PipelineAgent


class FakeSession:
    """Stands in for LLMSession; PipelineAgent never inspects it directly."""
    messages = []


def _fake_tool(*responses):
    """A callable that returns each response in turn, JSON-encoded."""
    calls = {"n": 0}

    def _tool(inputs):
        response = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return json.dumps(response)

    _tool.calls = calls
    return _tool


LOOK_OK = {"status": "success", "raw_sample": "id,name\n1,a", "rejected": False,
           "looker_spec": {"layout_type": "tabular_delimited"},
           "metadata_report": {"delimiter": ",", "modal_field_count": 2}}
THINK_OK = {"status": "success", "generated_code": {"code": "class DataExtractor: pass"}}
TEST_OK = {"status": "success", "extracted_rows": [{"id": "1", "_valid": True}], "total_rows": 1}
EVAL_PASS = {"status": "success", "extraction_passed": True, "should_retry": False}
EVAL_FAIL_RETRY = {"status": "success", "extraction_passed": False,
                   "should_retry": True, "failure_reason": "bad rows"}
EVAL_FAIL_STOP = {"status": "success", "extraction_passed": False,
                  "should_retry": False, "failure_reason": "bad rows"}


def _agent(guid="g", **tool_overrides):
    tools = {
        "fetch_and_sample": _fake_tool(LOOK_OK),
        "structural_inspector": _fake_tool(LOOK_OK),
        "generate_parser_script": _fake_tool(THINK_OK),
        "sandbox_execute": _fake_tool(TEST_OK),
        "evaluate_extraction": _fake_tool(EVAL_PASS),
        **tool_overrides,
    }
    return PipelineAgent(guid, tools=tools, llm_session=FakeSession())


def test_successful_run_in_one_attempt():
    agent = _agent()
    state = agent.run()

    assert state.status == "success"
    assert state.retry_count == 1
    assert state.extracted_rows == [{"id": "1", "_valid": True}]
    assert state.metadata_report["delimiter"] == ","

    print("✓ test_successful_run_in_one_attempt PASSED")


def test_looker_rejection_stops_before_thinker():
    """A document the Looker rejects never reaches generate_parser_script."""
    rejected_look = {**LOOK_OK, "rejected": True, "rejection_code": "NOT_TABULAR",
                     "rejection_reason": "prose, not a table"}
    thinker = _fake_tool(THINK_OK)
    agent = _agent(structural_inspector=_fake_tool(rejected_look),
                   generate_parser_script=thinker)

    state = agent.run()

    assert state.status == "rejected"
    assert state.rejection_code == "NOT_TABULAR"
    assert thinker.calls["n"] == 0, "the Thinker must never be called"

    print("✓ test_looker_rejection_stops_before_thinker PASSED")


def test_retry_loop_recovers_on_the_second_attempt():
    """Eval fails with should_retry once, then the same loop passes."""
    agent = _agent(evaluate_extraction=_fake_tool(EVAL_FAIL_RETRY, EVAL_PASS))
    state = agent.run()

    assert state.status == "success"
    assert state.retry_count == 2
    assert state.error_logs == ["bad rows"]

    print("✓ test_retry_loop_recovers_on_the_second_attempt PASSED")


def test_retry_ceiling_stops_the_loop():
    """Eval keeps saying retry, but the loop must stop at MAX_EXTRACTION_ATTEMPTS -
    the same constant evaluate_extraction itself reads, so the two cannot drift."""
    from tools.evaluate_extraction.tool import EvaluateExtractionTool
    assert EvaluateExtractionTool.MAX_ATTEMPTS == config.MAX_EXTRACTION_ATTEMPTS

    agent = _agent(evaluate_extraction=_fake_tool(EVAL_FAIL_RETRY))
    state = agent.run()

    assert state.status == "failed"
    assert state.retry_count == config.MAX_EXTRACTION_ATTEMPTS

    print("✓ test_retry_ceiling_stops_the_loop PASSED")


def test_eval_says_no_retry_stops_immediately():
    """When Eval itself says the failure is not retryable, the loop must not
    spend its remaining attempts anyway."""
    thinker = _fake_tool(THINK_OK)
    agent = _agent(generate_parser_script=thinker,
                   evaluate_extraction=_fake_tool(EVAL_FAIL_STOP))
    state = agent.run()

    assert state.status == "failed"
    assert state.retry_count == 1
    assert thinker.calls["n"] == 1, "must not have tried a second attempt"

    print("✓ test_eval_says_no_retry_stops_immediately PASSED")


def test_generation_failure_is_retried_with_feedback():
    """A failed generation gets a retry (not an abandon) with feedback set,
    and succeeds on the next attempt."""
    generation_failed = {"status": "error", "error": "vLLM timeout"}
    agent = _agent(generate_parser_script=_fake_tool(generation_failed, THINK_OK))
    state = agent.run()

    assert state.status == "success"
    assert state.retry_count == 2
    assert any("vLLM timeout" in e for e in state.error_logs)

    print("✓ test_generation_failure_is_retried_with_feedback PASSED")


def run_all_tests():
    tests = [
        test_successful_run_in_one_attempt,
        test_looker_rejection_stops_before_thinker,
        test_retry_loop_recovers_on_the_second_attempt,
        test_retry_ceiling_stops_the_loop,
        test_eval_says_no_retry_stops_immediately,
        test_generation_failure_is_retried_with_feedback,
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
