# Agentic Extraction Engine — instructions for Claude

Self-correcting extraction pipeline: an LLM writes a parser for each
document's own structure, a sandbox runs it, a second pass judges whether it
worked, and a retry feeds the failure back to the model instead of starting
over. Full docs live in `docs/` — start with `docs/README.md`.

## Orient here first

- `docs/README.md` — what this is, quick start
- `docs/ARCHITECTURE.md` — module layout, and which code is actually live
- `docs/TOOLS.md` — per-tool input/output reference
- `docs/PIPELINE_OPERATIONS.md` — running it, metrics, caching
- `docs/CLAUDE.md` — terminology and operating gotchas (the detailed version of this file)

## The live pipeline is `tools/` + `run_pipeline.py` + `run_corpus.py`

`orchestrator.py` and `extraction/phase1/`…`phase4/` are an earlier design
and are **not wired to anything live** — `orchestrator.py`'s own imports no
longer resolve. Don't extend them, and don't assume code found there
describes current behavior. The six tools and their order are in
`tools/__init__.py`'s docstring; `run_pipeline.py` chains them for one
document, `run_corpus.py` drains the backlog.

## Before touching pipeline code

- A local vLLM server must be reachable at `http://localhost:8000` for
  Tool 3 (`generate_parser_script`) and its tests — `curl
  http://localhost:8000/v1/models` to check. Most tool tests call the real
  server rather than mocking it; that's intentional for this repo, keep
  following that pattern in `tools/*/test_tool.py`.
- `cache/schema_code_cache.db` and `cache/column_labels.db` are shared
  runtime data, not fixtures. `get_cache().clear()` wipes the shared cache —
  fine inside a test process, don't run it against the shared file casually.
- Prefer running the actual test suite (`python -m pytest tools/
  population_selection/ extraction/`) over trusting a docstring or an old
  status-report file — several `.md` docs in this repo described a state
  that had already changed by the time this note was written; the code and
  its tests are the source of truth.

## Documentation conventions

Durable design rationale belongs in the module's own docstring (this
codebase's existing style — see `tools/*/tool.py`, `run_corpus.py`,
`extraction/core/llm_service.py`), not in a standalone status-report `.md`
file. Keep `docs/` as a thin index over that code, not a duplicate of it.
Point-in-time reports ("Tool N validated ✅", "ran on 50 documents on
2026-08-13") belong in a PR description or commit message, not as a
permanent root-level file — they go stale the moment the code they describe
changes again.
