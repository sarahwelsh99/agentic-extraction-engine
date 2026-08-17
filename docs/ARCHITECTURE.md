# Architecture

This describes the pipeline as it actually runs today. For *why* a given
module works the way it does, read that module's own docstring — most of the
design rationale lives there, not here.

## Layout

```
tools/                      Tools 1-6 (see docs/TOOLS.md)
  base.py                   AgentTool base class + ToolResponse
  __init__.py                 registry: get_all_tools(), get_tool_by_name(), PIPELINE order
  fetch_and_sample/
  delimiter_detector/
  generate_parser_script/
  sandbox_execute/
  evaluate_extraction/
  load_to_bigquery/
  write_parquet_to_gcs/     an alternative Tool 6 — not wired into the registry or run_pipeline.py

extraction/
  core/
    config.py                env-driven configuration
    bigquery_service.py      status-table reads/writes (population, queue, load)
    llm_service.py           LocalLLMClient + LLMSession (vLLM client)
    workqueue.py             SQLite work queue, LPT bin-packing (from mosaic)
    records.py               shared record/row dataclasses
  metrics_recorder.py         per-run metrics -> metrics.csv / metrics.json
  schema_code_cache.py         SQLite cache of generated parsers, keyed by schema hash
  pipeline_analyzer.py         validates Tools 1-4 output for the test harness

population_selection/        standalone regex PII pre-filter, run before extraction
run_pipeline.py               run Tools 1-6 on one document
run_corpus.py                 drain the whole pending backlog through run_pipeline.py's logic
scripts/provision_extraction_table.py

retired/                     superseded tools, kept for reference (see retired/README.md)
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
pipeline order:

```
1  fetch_and_sample        fetch the source and take a representative sample
2  delimiter_detector      report how the document is laid out
3  generate_parser_script  write a deterministic parser from that report
4  sandbox_execute         run the parser over the whole document
5  evaluate_extraction     decide whether the extraction worked
6  load_to_bigquery        load a passing extraction, one table per guid
```

`run_pipeline.run_pipeline(guid)` chains all six for one document, including
the generate → sandbox → evaluate retry loop (`MAX_EXTRACTION_ATTEMPTS`,
currently 2) with an `LLMSession` shared across retries so a retry is a short
follow-up turn against the code and failure already generated, not a rebuilt
prompt (see `extraction/core/llm_service.py`).

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

Only `fetch_and_sample` and `delimiter_detector` inherit `AgentTool` /
`ToolResponse` from `tools/base.py`. The rest (`generate_parser_script`,
`sandbox_execute`, `evaluate_extraction`, `load_to_bigquery`) are plain
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

## Testing

Each tool's tests live next to it (`tools/<name>/test_tool.py`) and are
pytest-discoverable. `test_tools_1_to_4.py` at the repo root is a separate,
heavier harness that runs Tools 1-4 against real documents fetched from
`glean` and validates output with `extraction/pipeline_analyzer.py` — see
`docs/PIPELINE_OPERATIONS.md`.
