"""Tests for LLMSession (extraction/core/llm_service.py).

LLMSession is the stateful counterpart to LocalLLMClient.generate(): it holds
a growing messages list across a document's retry loop. These tests use a
fake client so they assert on turn bookkeeping, never on model output.
"""

from extraction.core.llm_service import LLMSession


class FakeClient:
    """Records every messages list it is called with; replies are scripted."""

    def __init__(self, replies=None, fail_on=None):
        self.replies = list(replies) if replies is not None else []
        self.fail_on = fail_on or set()
        self.calls = []

    def chat(self, messages, temperature=0.0, max_tokens=2000, json_schema=None):
        self.calls.append([dict(m) for m in messages])
        call_index = len(self.calls) - 1
        if call_index in self.fail_on:
            raise RuntimeError("simulated transport failure")
        return self.replies[call_index]


def test_first_send_carries_the_system_prompt():
    """A session opened with a system prompt sends it on the very first call."""
    client = FakeClient(replies=["reply-1"])
    session = LLMSession(client, system_prompt="You write parsers.")

    session.send("generate code")

    assert client.calls[0] == [
        {"role": "system", "content": "You write parsers."},
        {"role": "user", "content": "generate code"},
    ]
    print("✓ test_first_send_carries_the_system_prompt PASSED")


def test_each_send_replays_the_full_history():
    """A retry sees its own earlier code and the reply that followed it."""
    client = FakeClient(replies=["code-v1", "code-v2"])
    session = LLMSession(client)

    session.send("generate code")
    session.send("that failed, fix it")

    second_call = client.calls[1]
    assert second_call == [
        {"role": "user", "content": "generate code"},
        {"role": "assistant", "content": "code-v1"},
        {"role": "user", "content": "that failed, fix it"},
    ]
    print("✓ test_each_send_replays_the_full_history PASSED")


def test_send_records_both_turns_on_success():
    """After a successful send, the reply is in history for the next turn."""
    client = FakeClient(replies=["code-v1"])
    session = LLMSession(client)

    reply = session.send("generate code")

    assert reply == "code-v1"
    assert session.messages == [
        {"role": "user", "content": "generate code"},
        {"role": "assistant", "content": "code-v1"},
    ]
    print("✓ test_send_records_both_turns_on_success PASSED")


def test_failed_send_rolls_back_the_user_turn():
    """A transport failure must not leave a dangling user turn with no reply.

    Otherwise a caller that retries send() would build up consecutive user
    turns with nothing answering them in between.
    """
    client = FakeClient(replies=[None, "code-v1"], fail_on={0})
    session = LLMSession(client)

    try:
        session.send("generate code")
        assert False, "expected the simulated failure to raise"
    except RuntimeError:
        pass

    assert session.messages == [], session.messages

    # A subsequent, successful send starts clean rather than stacked on debris.
    session.send("generate code")
    assert client.calls[1] == [{"role": "user", "content": "generate code"}]
    print("✓ test_failed_send_rolls_back_the_user_turn PASSED")


def run_all_tests():
    tests = [
        test_first_send_carries_the_system_prompt,
        test_each_send_replays_the_full_history,
        test_send_records_both_turns_on_success,
        test_failed_send_rolls_back_the_user_turn,
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
