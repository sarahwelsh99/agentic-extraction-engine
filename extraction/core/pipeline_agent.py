"""The pipeline as an explicit state machine: Looker -> Thinker -> Tester -> Eval.

Looker runs once (fetch_and_sample's Micro-Slicer, then structural_inspector),
then Thinker -> Tester -> Eval loop with feedback until Eval passes or the
retry ceiling (config.MAX_EXTRACTION_ATTEMPTS) is reached:

    [Looker]  fetch_and_sample + structural_inspector, once
        |
    [Thinker] generate_parser_script  <---------------.
        |                                              |
    [Tester]  sandbox_execute                           | retry, with the
        |                                              | failure as feedback
    [Eval]    evaluate_extraction  --- failed, retry --'
        |
     passed
        |
    caller delivers (write_parquet_to_gcs)

This replaces the loose local variables (feedback, attempt, llm_session) that
used to be threaded through run_pipeline.py's while loop with one typed
PipelineState, and collapses the retry ceiling that used to be duplicated
between run_pipeline.py and evaluate_extraction/tool.py into the single
config.MAX_EXTRACTION_ATTEMPTS both now read.

Delivery (Tool 6) is deliberately not a state here - a passing PipelineState
is handed to the caller (run_pipeline.py), which loads it in a batch alongside
other documents rather than one at a time (see write_parquet_to_gcs and
run_corpus.py's per-bin batching).

Sheets and concurrency: a workbook flattened into one body_text can carry
several worksheets (extraction/core/records.py's split_sheets()), each with
its own structure. run_document() below is the fan-out: it splits body_text
into per-sheet blocks and runs one PipelineAgent per sheet *concurrently*,
via real asyncio (httpx.AsyncClient for the LLM calls, asyncio.subprocess for
the Docker sandbox) rather than threads - see structural_inspector,
generate_parser_script, and sandbox_execute's own acall() methods. A
single-sheet document (the common case) is just the len(sheets) == 1 case of
the same code path, not a special case.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from extraction.core import config
from extraction.core.llm_service import get_llm_client, LLMSession
from extraction.core.records import split_sheets
from extraction.core.sheet_pii import classify_sheet_text
from tools import get_tool_by_name
import json


def _require_tool(name: str):
    """Fetch a tool, failing with the reason rather than a NoneType error."""
    tool = get_tool_by_name(name)
    if tool is None:
        raise RuntimeError(
            f"Tool '{name}' could not be created. Check its configuration "
            f"(see the error logged above)."
        )
    return tool


@dataclass
class PipelineState:
    """State carried through one document's run of the agent loop."""

    guid: str
    status: str = "running"  # running | success | failed | rejected
    # None for an ordinary, single-table document; the worksheet's own name
    # when this state came from run_document()'s per-sheet fan-out.
    sheet_name: Optional[str] = None
    looker_spec: Optional[Dict[str, Any]] = None
    metadata_report: Dict[str, Any] = field(default_factory=dict)
    generated_code: Optional[Dict[str, Any]] = None
    error_logs: List[str] = field(default_factory=list)
    retry_count: int = 0
    extracted_rows: List[Dict[str, Any]] = field(default_factory=list)
    rejection_code: Optional[str] = None
    rejection_reason: Optional[str] = None
    failure_reason: Optional[str] = None
    should_retry: bool = False
    # One entry per tool call, in order: {"stage", "attempt", "start", "end",
    # "status", "response"} - the raw tool response, so the caller
    # (run_pipeline.py) can pull whatever fields its own metrics CSV wants
    # without this module needing to know their names.
    stage_log: List[Dict[str, Any]] = field(default_factory=list)
    # Set by run_document() from extraction.core.sheet_pii.classify_sheet_text()
    # against this sheet's own raw text - independent of whether the sheet
    # passed, failed, or was rejected, so a rejected (non-tabular, or empty)
    # sheet still gets a PII signal even though it never reaches Thinker/Tester.
    has_pii: bool = False
    pii_score: int = 0
    pii_signals: str = ""


class PipelineAgent:
    """Drives one document through Looker -> Thinker -> Tester -> Eval."""

    def __init__(
        self,
        guid: str,
        body_text: Optional[str] = None,
        tools: Optional[Dict[str, Any]] = None,
        llm_session: Optional[LLMSession] = None,
    ):
        """
        Args:
            tools: optional override of {tool_name: callable}, for tests.
                fetch_and_sample/evaluate_extraction callables are sync
                (input dict -> JSON string, same as their __call__).
                structural_inspector/generate_parser_script/sandbox_execute
                callables are async (input dict -> awaitable of JSON
                string, same as their acall()) - these three are the ones
                that actually do I/O and run concurrently across a
                document's sheets. Any name not given falls back to the
                real, registered tool.
            llm_session: optional override, so tests need not reach vLLM.
        """
        self.guid = guid
        self.body_text = body_text
        self.state = PipelineState(guid=guid)

        tools = tools or {}
        # Sync: no I/O (fetch_and_sample given body_text directly) or pure
        # computation (evaluate_extraction) - no async twin needed.
        self._tool1 = tools.get("fetch_and_sample") or _require_tool("fetch_and_sample")
        self._tool5 = tools.get("evaluate_extraction") or _require_tool("evaluate_extraction")
        # Async: these do the real I/O (LLM calls, the Docker sandbox), and
        # are what run_document()'s per-sheet fan-out actually parallelizes.
        # Bound to .acall so PipelineAgent always calls a plain
        # input-dict-in, coroutine-out callable regardless of whether it's a
        # real tool or a test fake.
        self._tool2 = tools.get("structural_inspector") or _require_tool("structural_inspector").acall
        self._tool3 = tools.get("generate_parser_script") or _require_tool("generate_parser_script").acall
        self._tool4 = tools.get("sandbox_execute") or _require_tool("sandbox_execute").acall

        # One conversation for this document's whole generate-validate-retry
        # loop: a retry is "fix this" against the code and failure Tool 3
        # already saw, not a rebuilt prompt. Scoped to this guid; dropped
        # when the loop ends.
        self._llm_session = llm_session if llm_session is not None else LLMSession(get_llm_client())
        self._tool1_response: Dict[str, Any] = {}

    async def run(self) -> PipelineState:
        """Run the whole loop for this document. Always returns the final state."""
        if not await self._look():
            return self.state

        while self.state.retry_count < config.MAX_EXTRACTION_ATTEMPTS:
            self.state.retry_count += 1

            if not await self._think():
                if self.state.status != "running":
                    return self.state
                continue  # generation failed this attempt; retry with feedback

            test_response = await self._test()
            if test_response is None:
                return self.state  # the tool call itself broke, not the script

            if await self._eval(test_response):
                self.state.status = "success"
                return self.state
            if self.state.status != "running" or not self.state.should_retry:
                break

        self.state.status = "failed"
        return self.state

    def _log(self, stage: str, start: float, status: str, response: Dict[str, Any]) -> None:
        self.state.stage_log.append({
            "stage": stage,
            "attempt": self.state.retry_count,
            "start": start,
            "end": time.time(),
            "status": status,
            "response": response,
        })

    # ---------- Looker: fetch_and_sample (Micro-Slicer) + structural_inspector ----------
    async def _look(self) -> bool:
        # A sheet with a name and a header marker but no data rows after it
        # (routinely the workbook's last tab) reconstructs to an empty
        # body_text in split_sheets(). That's "nothing to extract," not a
        # missing input - reject it the same way structural_inspector
        # already rejects a document with no data rows, rather than letting
        # it reach fetch_and_sample's generic "must provide body_text" check.
        if not self.body_text:
            self.state.status = "rejected"
            self.state.rejection_code = "NO_DATA_ROWS"
            self.state.rejection_reason = "Sheet contained no data rows"
            return False

        start = time.time()
        # Sync: fetch_and_sample does no network I/O when given body_text
        # directly, which is always how a per-sheet agent invokes it.
        response = json.loads(self._tool1({
            "guid": self.guid,
            "body_text": self.body_text,
            "sample_size": 5,
        }))
        self._log("look_slice", start, response.get("status", "error"), response)
        if response.get("status") != "success":
            self.state.status = "failed"
            self.state.failure_reason = f"fetch_and_sample failed: {response.get('error')}"
            return False
        self._tool1_response = response

        start = time.time()
        inspected = json.loads(await self._tool2({
            "guid": self.guid,
            "raw_sample": response["raw_sample"],
            "sampled_record_indices": response.get("sampled_record_indices"),
            "sheet_names": response.get("sheet_names"),
            "total_records": response.get("total_records"),
            "total_bytes": response.get("total_bytes"),
            "encoding": response.get("encoding"),
        }))
        self._log("look_inspect", start, inspected.get("status", "error"), inspected)
        if inspected.get("status") != "success":
            self.state.status = "failed"
            self.state.failure_reason = f"structural_inspector failed: {inspected.get('error')}"
            return False

        if inspected.get("rejected"):
            self.state.status = "rejected"
            self.state.rejection_code = inspected.get("rejection_code")
            self.state.rejection_reason = inspected.get("rejection_reason")
            return False

        self.state.looker_spec = inspected.get("looker_spec")
        self.state.metadata_report = inspected.get("metadata_report", {})
        return True

    # ---------- Thinker: generate_parser_script ----------
    async def _think(self) -> bool:
        start = time.time()
        feedback = self.state.error_logs[-1] if self.state.error_logs else None
        response = json.loads(await self._tool3({
            "guid": self.guid,
            "raw_sample": self._tool1_response.get("raw_sample"),
            "metadata_report": self.state.metadata_report,
            "feedback": feedback,
            "attempt": self.state.retry_count,
            "session": self._llm_session,
        }))
        self._log("think", start, response.get("status", "error"), response)

        if response.get("status") == "skipped":
            self.state.status = "rejected"
            self.state.rejection_reason = response.get("error")
            return False

        if response.get("status") != "success":
            # Generation is not deterministic, so it can fail on a document it
            # would succeed on next time - worth a retry, not an abandon.
            self.state.error_logs.append(
                f"The previous attempt did not produce usable code: "
                f"{response.get('error')}. Write straightforward code and "
                f"make sure parse_row returns its result."
            )
            if self.state.retry_count >= config.MAX_EXTRACTION_ATTEMPTS:
                self.state.status = "failed"
                self.state.failure_reason = f"Could not generate a script: {response.get('error')}"
            return False

        self.state.generated_code = response.get("generated_code", {})
        return True

    # ---------- Tester: sandbox_execute ----------
    async def _test(self) -> Optional[Dict[str, Any]]:
        start = time.time()
        response = json.loads(await self._tool4({
            "guid": self.guid,
            "generated_code": self.state.generated_code.get("code"),
            # The full document, not Tool 1's slice: the slice bounds the
            # Looker's LLM call, not what gets extracted.
            "body_text": self.body_text or self._tool1_response.get("raw_sample"),
            "metadata_report": self.state.metadata_report,
        }))
        self._log("test", start, response.get("status", "error"), response)
        return response

    # ---------- Eval: evaluate_extraction ----------
    async def _eval(self, test_response: Dict[str, Any]) -> bool:
        # No I/O here (pure comparison logic) - async only for a uniform
        # calling convention in run(); nothing below actually awaits.
        start = time.time()
        response = json.loads(self._tool5({
            "guid": self.guid,
            "execution_result": test_response,
            "metadata_report": self.state.metadata_report,
            "attempt": self.state.retry_count,
        }))
        self._log("eval", start, response.get("status", "error"), response)

        if response.get("status") != "success":
            self.state.status = "failed"
            self.state.failure_reason = f"evaluate_extraction failed: {response.get('error')}"
            return False

        passed = bool(response.get("extraction_passed"))
        self.state.failure_reason = response.get("failure_reason")
        self.state.should_retry = bool(response.get("should_retry"))

        if passed:
            self.state.extracted_rows = test_response.get("extracted_rows", [])
            return True

        self.state.error_logs.append(self.state.failure_reason or "extraction failed")
        return False


async def run_document(
    guid: str,
    body_text: str,
    tools: Optional[Dict[str, Any]] = None,
    llm_session_factory: Optional[Any] = None,
) -> List[PipelineState]:
    """Detect worksheets and run each one through the agent loop concurrently.

    A document with no SHEET_MARKER rows at all (the common case) is a
    single sheet block with sheet_name=None - this always goes through the
    same fan-out, never a special case, and returns a list of exactly one
    PipelineState identical to calling PipelineAgent directly.

    A multi-sheet document runs one PipelineAgent per sheet, each with its
    own body_text scoped to that sheet alone, and each with its own
    LLMSession (a session is one document/sheet's own retry conversation -
    sharing one across concurrent sheets would interleave unrelated turns).
    They run genuinely concurrently via asyncio.gather - the I/O each one
    does (structural_inspector/generate_parser_script's LLM calls,
    sandbox_execute's Docker container) is all async under the hood.

    Args:
        tools: passed through to every sheet's PipelineAgent - see its own
            docstring for the sync/async split test doubles must follow.
        llm_session_factory: called once per sheet to build that sheet's own
            LLMSession - defaults to LLMSession(get_llm_client()). Tests pass
            a fake session factory so no sheet touches the real vLLM
            singleton.

    Returns:
        One PipelineState per sheet, in document order.
    """
    blocks = split_sheets(body_text)
    session_factory = llm_session_factory or (lambda: LLMSession(get_llm_client()))

    agents = []
    for block in blocks:
        agent = PipelineAgent(
            guid, body_text=block.body_text, tools=tools,
            llm_session=session_factory(),
        )
        agent.state.sheet_name = block.name
        # A pure function of this sheet's own raw text, independent of
        # whether it goes on to pass, fail, or get rejected - reusing
        # population_selection's own regex categories (and its "a bare name
        # alone isn't PII" nuance, inherited for free since there's no name
        # category to match) rather than a second, LLM-based judgment call.
        pii = classify_sheet_text(block.body_text)
        agent.state.has_pii = pii["has_pii"]
        agent.state.pii_score = pii["pii_score"]
        agent.state.pii_signals = pii["pii_signals"]
        agents.append(agent)

    return list(await asyncio.gather(*(agent.run() for agent in agents)))
