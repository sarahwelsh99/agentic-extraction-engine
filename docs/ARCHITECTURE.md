# Architecture

This describes the pipeline as it actually runs today. For *why* a given
module works the way it does, read that module's own docstring — most of the
design rationale lives there, not here.

## Layout

```
tools/                      Looker/Thinker/Tester/Eval/Deliver (see docs/TOOLS.md)
  base.py                   AgentTool base class + ToolResponse
  __init__.py                 registry: get_all_tools(), get_tool_by_name(), PIPELINE order
  fetch_and_sample/          Looker: Micro-Slicer (code) - bounded head+tail slice
  structural_inspector/     Looker: Structural Inspector (LLM) - header/footer/delimiter/nulls
  generate_parser_script/   Thinker
  sandbox_execute/          Tester
  evaluate_extraction/      Eval
  write_parquet_to_gcs/     Deliver - one Parquet file per guid

extraction/
  core/
    config.py                env-driven configuration
    bigquery_service.py      status-table reads/writes (population, queue)
    llm_service.py           LocalLLMClient + LLMSession (vLLM client, sync + async)
    pipeline_agent.py        the state machine: PipelineState + PipelineAgent,
                              run_document() (async per-sheet fan-out)
    workqueue.py             SQLite work queue, LPT bin-packing (from mosaic)
    records.py               shared record/row splitting + split_sheets()
  metrics_recorder.py         per-run metrics -> metrics.csv / metrics.json
  schema_code_cache.py         SQLite cache of generated parsers, keyed by schema hash
  pipeline_analyzer.py         validates Tools 1-4 output for the test harness

population_selection/        standalone regex PII pre-filter, run before extraction
run_pipeline.py               drive PipelineAgent for one document, then deliver
run_corpus.py                 drain the whole pending backlog through run_pipeline.py's logic

retired/                     superseded tools, kept for reference (see retired/README.md)
                              - includes delimiter_detector (Looker's earlier
                                heuristic form) and load_to_bigquery (the
                                earlier delivery step)
cache/                       schema_code_cache.db, column_labels.db (gitignored data, not code)
```

### Legacy, not part of the live pipeline

`orchestrator.py` and `extraction/phase1/` … `extraction/phase4/` are an
earlier Phase 1-4 design (LLM code generation once, then deterministic
execution at scale) that predates the `tools/` pipeline. They are not called
by `run_pipeline.py`, `run_corpus.py`, or anything under `tools/`, and
`orchestrator.py`'s own imports (`from phase1.analyzer import ...`) no longer
resolve. Nothing here currently deletes or fixes them — treat them as
historical, not as a second live pipeline.

## The live pipeline

`tools/__init__.py`'s docstring is the shortest accurate description of the
pipeline order, named after the state machine's roles:

```
1  fetch_and_sample        Looker (Micro-Slicer): bounded head+tail slice
2  structural_inspector    Looker (Structural Inspector): LLM structural spec
3  generate_parser_script  Thinker: write a deterministic parser from that spec
4  sandbox_execute         Tester: run the parser over the whole document
5  evaluate_extraction     Eval: decide whether the extraction worked
6  write_parquet_to_gcs    Deliver: write a passing extraction, one file per guid
```

`extraction/core/pipeline_agent.py`'s `PipelineAgent` is the state machine
itself: Looker runs once (`_look()`), then Thinker → Tester → Eval loop
(`_think()` → `_test()` → `_eval()`) with feedback until Eval passes or the
retry ceiling (`config.MAX_EXTRACTION_ATTEMPTS`, currently 2 - the single
source of truth also read by `tools/evaluate_extraction/tool.py`) is reached,
with an `LLMSession` shared across retries so a retry is a short follow-up
turn against the code and failure already generated, not a rebuilt prompt
(see `extraction/core/llm_service.py`). All of this state lives in one typed
`PipelineState` (`looker_spec`, `metadata_report`, `error_logs`,
`retry_count`, `extracted_rows`, `sheet_name`, ...) rather than loose local
variables. `PipelineAgent` is async (`await agent.run()`) — see "Sheets and
concurrency" below.

### Sheets and concurrency

A single glean document can flatten several worksheets into one `body_text`
(a workbook with a tab per agent/region/etc.), each with its own header and
structure. `extraction/core/records.py`'s `split_sheets()` detects this
mechanically — the boundary (`SHEET_MARKER`-terminated rows) is unambiguous,
no LLM judgment needed — and `pipeline_agent.run_document(guid, body_text)`
fans out: one full `PipelineAgent` per sheet, each with its own body_text
scoped to that sheet alone and its own `LLMSession`, run **concurrently** via
real asyncio (`httpx.AsyncClient` for the LLM calls in `structural_inspector`
and `generate_parser_script`, `asyncio.create_subprocess_exec` for
`sandbox_execute`'s Docker sandbox — see each tool's `acall()` method,
alongside its original sync `__call__`). A single-sheet document (the common
case) is just the `len(sheets) == 1` case of the same code path.

`run_pipeline.run_pipeline(guid)` calls `run_document()` (bridging into the
event loop via `asyncio.run()` — everything above this point, including
`run_corpus.py`'s own per-*document* concurrency via `ThreadPoolExecutor`,
stays synchronous and untouched), then merges every sheet's result: rows
from every *passing* sheet are combined (tagged `_sheet_name` when there was
more than one sheet), and **partial success counts** — one guid is delivered
and marked complete if at least one sheet passed, with the specific
pass/fail/rejected outcome per sheet (and which step it failed at) reported
under `results["sheets"]`. It owns logging, metrics recording, and the CLI,
not the state machine's control flow.

`run_corpus.py` drains the backlog: it reads the status table population
selection populated (never `glean.drive_files` directly), bin-packs pending
guids into `extraction/core/workqueue.py`'s local SQLite queue, and calls
`run_pipeline`'s logic per bin. Its own module docstring has the full
phase-by-phase breakdown (`fetch_pending_totals` → `fetch_pending_metadata` →
build queue → `fetch_bodies_for_guids` per bin) and is worth reading directly
rather than restated here.

`population_selection/` runs once before either of the above, independent of
everything else in this list — see `docs/CLAUDE.md` for what it does and why.

## Tool interface

Only `fetch_and_sample` inherits `AgentTool` / `ToolResponse` from
`tools/base.py`. The rest (`structural_inspector`, `generate_parser_script`,
`sandbox_execute`, `evaluate_extraction`, `write_parquet_to_gcs`) are plain
classes whose `__call__` returns a JSON string directly — both shapes are
called the same way by the registry:

```python
from tools import get_all_tools, get_tool_by_name

tool = get_tool_by_name("fetch_and_sample")
response = json.loads(tool({"guid": "..."}))
```

`get_all_tools()` skips any tool that can't be constructed without external
config (e.g. missing credentials) rather than raising, so it stays usable
without a fully configured environment.

`structural_inspector`, `generate_parser_script`, and `sandbox_execute` — the
three that actually do I/O (an LLM call, or the Docker sandbox) — each also
expose `async def acall(inputs) -> str`, alongside their original sync
`__call__`, with the identical contract. `acall()` is what
`pipeline_agent.py`'s per-sheet fan-out uses so a document's sheets run
concurrently; nothing else calls it, and `__call__` still works exactly as
it always has for any other caller. `fetch_and_sample` (no I/O once given
`body_text`) and `evaluate_extraction` (pure comparison, no I/O at all) have
no async twin — there's nothing to gain from one.

## Testing

Each tool's tests live next to it (`tools/<name>/test_tool.py`, including an
`asyncio.run(...)`-wrapped case for the async `acall()` path). The state
machine's own tests live at `extraction/core/test_pipeline_agent.py` (every
tool faked - sync for `fetch_and_sample`/`evaluate_extraction`, async for the
rest - pinning retry/rejection transitions *and* the sheet fan-out, including
a test that proves sheets actually run concurrently rather than sequentially).
`extraction/core/test_records.py` covers `split_sheets()`/`has_multiple_sheets()`
against the real multi-sheet shape found in production. `test_run_pipeline.py`
at the repo root covers `run_pipeline.py`'s own merge logic (row tagging,
partial-success semantics) with `run_document()` faked. All of the above are
pytest-discoverable, and there's no `pytest-asyncio` dependency - async test
bodies are plain functions that call `asyncio.run(...)` themselves, matching
this repo's existing plain-function-test style.

`test_tools_1_to_4.py` at the repo root is a separate, heavier harness that
runs Tools 1-4 against real documents fetched from `glean` and validates
output with `extraction/pipeline_analyzer.py` — see
`docs/PIPELINE_OPERATIONS.md`.
