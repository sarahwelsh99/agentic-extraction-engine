# Operating Notes for Agentic Extraction Pipeline

For what runs and how, see [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md),
[TOOLS.md](TOOLS.md), and [PIPELINE_OPERATIONS.md](PIPELINE_OPERATIONS.md).
This page is terminology, gotchas, and things that are easy to get wrong.

## Key Concepts

### Six tools, not four phases

An earlier design (`orchestrator.py`, `extraction/phase1/`…`phase4/`) split
the work into four phases: LLM code generation once, then deterministic
execution at scale. That design is not live — see "Legacy, not part of the
live pipeline" in [ARCHITECTURE.md](ARCHITECTURE.md). The pipeline that
actually runs is `run_pipeline.py` chaining Tools 1-6
(`fetch_and_sample → delimiter_detector → generate_parser_script →
sandbox_execute → evaluate_extraction → load_to_bigquery`) per document,
with a generate → sandbox → evaluate retry loop bounded by
`MAX_EXTRACTION_ATTEMPTS`. Don't reach for `orchestrator.py` or `phase1-4` —
they aren't wired to anything and `orchestrator.py`'s own imports are
already broken.

### Data Source Terminology

All data comes from `glean.drive_files` filtered by:
```sql
WHERE triage_category = 'INCL_STRUCTURED_RECORD'
```

This is a **single, fixed source**.

### Extraction vs. inference vs. code generation

- **Extraction**: Taking data from a document (what the pipeline does end to end)
- **Inference**: Running the vLLM model forward — Tool 3 (parser generation)
  and, indirectly, any LLM-judged step
- **Code generation**: Tool 3 asking the model to synthesize a Python parser,
  cached by schema shape so it happens once per shape, not once per document
- **Deterministic execution**: Tool 4 running that generated code — no LLM
  calls, sandboxed, one document at a time

## Population Selection (Runs Before Tool 1)

`population_selection/` is a standalone module — it doesn't import
`orchestrator.py`, `run_pipeline.py`, `phase1-4`, or `tools/` — that decides which
`glean.drive_files` rows (`triage_category = 'INCL_STRUCTURED_RECORD'`)
actually contain PII and should be extracted. One-time, but safe to rerun.

```bash
python -m population_selection                            # dry run: report counts
python -m population_selection --execute                   # run the regex pass
python -m population_selection --execute --source-limit 500  # smoke test on a slice
```

One set-based MERGE, no per-row BigQuery round trips and **no LLM calls at
all**: scores each document's `body_text` against 9 PII categories (DOB,
government ID, address, financial account, health/biometric, credential,
device ID, person ID, personal email) and flags it `pending` the moment
**any single category** matches, `excluded_no_pii` when none do. A bare
person name, or a name plus only contact info (email/phone), is deliberately
NOT enough on its own — see `population_selection/selector.py`'s module
docstring for the full reasoning.

The category patterns and this any-match rule aren't invented here — they're
ported directly from `mosaic-glean-extraction`'s `extraction/prefilter.py`
(Shubhankar Dash), the production-validated version of this exact decision.
Re-running that repo's own backtest methodology against 20,000 real
`glean.drive_files` guids with real extraction ground truth (from
`glean_extract.pii_extraction`) measured a 77.7% skip rate at a 1.85%
false-negative rate among skipped docs — most of them `PERSON_DATE_OF_BIRTH`-
only hits, which are a documented, known case of the extraction LLM
hallucinating ordinary business dates as DOB on drive documents (noise in
the ground truth, not real misses).

Rerunning only touches rows still in `pending` / `excluded_no_pii` (plus the
now-retired `needs_llm_review`, kept in the MATCHED guard purely to sweep up
any row an earlier version of this module left there) — anything
`run_pipeline.py`/`run_corpus.py` has already completed or errored on is left
untouched, so tuning the regex patterns or re-running after new source rows
land is always safe.

## Status Table Schema

The `pii_extraction_status` table (name from `config.SOURCE_TABLE_NAME`)
tracks population selection and extraction progress:

| Column | Type | Meaning |
|---|---|---|
| `guid` | STRING | Document ID |
| `status` | STRING | `excluded_no_pii` \| `pending` \| `complete` \| `error_*` \| `dense` \| `oversized` |
| `extraction_version` | STRING | Version of code that processed this (e.g., `v1.0`) |
| `extracted_at` | TIMESTAMP | When extraction completed |
| `error_message` | STRING | Error details if status is `error_*` |
| `body_length` | INTEGER | Size of input document |
| `body_text` | STRING | The actual document text |
| `source` | STRING | Source table name (`drive_files`); lets more than one population share this table |
| `pii_score` | INTEGER | Count of PII categories matched (population selection) |
| `pii_signals` | STRING | Comma-joined category names matched, e.g. `DOB,ADDRESS` |
| `pii_detection_method` | STRING | `regex` (population selection's only detection method) |

`extraction/core/bigquery_service.py`'s `initialize_status_table()` and its
own docstring are the source of truth if this table ever drifts from what's
written here.

### Status Values

- `excluded_no_pii` — population selection's regex pass found no PII category signal; will not be extracted
- `pending` — flagged as containing PII, not yet processed by extraction
- `complete` — successfully extracted
- `error_llm` — vLLM call failed
- `error_truncated` — output was cut off at token cap
- `error_oversized` — document exceeds max size, skipped entirely
- `dense` — document classified as structured (not prose), skipped
- `no_body` — document had no extractable text

## Output

Tool 6 (`load_to_bigquery`) loads every document's rows into **one shared
BigQuery table**, partitioned by extraction date and clustered by guid, with
each document's own columns carried in a JSON column. There is no per-phase
GCS artifact structure (no `extractors_v<N>.py`, no status ledger, no
async GCS writer) in the live pipeline — that belonged to the legacy
Phase 4 design. See [TOOLS.md](TOOLS.md#tool-6--load_to_bigquery) for the
append-and-dedup-at-read pattern, and note `write_parquet_to_gcs` as the
unwired alternative that writes one Parquet file per document instead.

## Local vLLM Configuration

- **Model**: `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8` (30B params, code-tuned)
- **Endpoint**: `http://localhost:8000` (OpenAI-compatible API)
- **Timeout**: 300s per request
- **Temperature**: 0.0 for Tool 3's first generation, 0.3 for retries (more room to try something different after a failure)
- **Max tokens**: ~2000 for a generated parser (`GenerateParserScriptTool._output_budget`)

If vLLM is unavailable:
```bash
curl http://localhost:8000/v1/models   # check status
ps aux | grep vllm                     # check process
```

## Common Mistakes

### Mistake 1: Reaching for `orchestrator.py`
`orchestrator.py` and `extraction/phase1-4/` are legacy and not wired to the
current pipeline — see [ARCHITECTURE.md](ARCHITECTURE.md). Use
`run_pipeline.py` (one document) or `run_corpus.py` (the backlog).

### Mistake 2: Running `run_corpus.py` before population selection
`run_corpus.py` only drains rows already marked `pending` in the status
table. If nothing's pending, run
`python -m population_selection --execute` first.

### Mistake 3: Assuming a cache hit means the schema is correct
Tool 3's cache key is the document's *structure* (delimiter, header shape,
field counts), not its meaning. A cache hit means "a parser for this shape
exists," not "this parser is right for this document" — that's still
Tool 5's job to verify per document.

### Mistake 4: Clearing the schema/code cache in a shared environment
`get_cache().clear()` (used by tests) wipes `cache/schema_code_cache.db` for
everyone using that file, including cached parsers other work depends on.
Fine in a test's own temp cache or a throwaway environment; don't run it
against the shared `cache/` directory casually.

## Cost / performance intuition

Tool 3 (LLM generation) is the expensive, GPU-bound step; a cache hit skips
it entirely. Tools 1, 2, 4, 5, 6 are deterministic/local (BigQuery I/O,
sandboxed Python, or a BigQuery load) and don't call vLLM. There's no
maintained cost model doc for this pipeline the way the legacy Phase 1-4
design had one — treat any per-document dollar figure you find in old
material as describing the *old* design, not this one.

## Questions?

See [README.md](README.md) for quick start, [ARCHITECTURE.md](ARCHITECTURE.md)
for module layout, and [TOOLS.md](TOOLS.md) /
[PIPELINE_OPERATIONS.md](PIPELINE_OPERATIONS.md) for the rest.
