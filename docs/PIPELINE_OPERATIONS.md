# Running and monitoring the pipeline

See `docs/ARCHITECTURE.md` for what each script calls. This is the "how do I
actually run/inspect this" reference.

## One document

```bash
python run_pipeline.py <guid>
python run_pipeline.py <guid> --body-text "..."   # skip Tool 1's fetch
python run_pipeline.py <guid> --json-output results.json --log-dir logs
```

## The whole backlog

```bash
python -m population_selection                      # dry run: report counts
python -m population_selection --execute             # flag pending/excluded_no_pii

python run_corpus.py --dry-run                       # size the backlog, build nothing
python run_corpus.py --limit 50                      # drain 50 documents
python run_corpus.py                                 # drain the backlog
python run_corpus.py --requeue-errors                # retry parked failures first
```

`run_corpus.py`'s own module docstring has the full phase breakdown
(`fetch_pending_totals` → `fetch_pending_metadata` → bin-pack → drain each bin)
— read it directly if you're touching that path.

Nothing needs provisioning first: `write_parquet_to_gcs` writes into
`GCS_OUTPUT_BUCKET`/`GCS_OUTPUT_PREFIX` directly, with no schema to create
ahead of time (see `docs/TOOLS.md`). The earlier BigQuery loader's
provisioning step is retired along with it — `retired/provision_extraction_table.py`.

## Metrics

`extraction/metrics_recorder.record_pipeline_run(...)` is called at the end
of every `run_pipeline.py` run and writes to two files at the repo root:

- **`metrics.csv`** — append-only, one row per tool per pipeline run
  (`ts, guid, source, batch_id, tool, duration_sec, rows_extracted, success`).
- **`metrics.json`** — running aggregates per source (`runs`,
  `total_rows_extracted`, `overall_rate_rows_per_sec`, `last_run`).

Both use cross-process file locking, so concurrent workers can write safely.
Read them back with `get_metrics_summary()` / `get_csv_records()` rather than
parsing the files by hand. `.metrics.lock` next to them is the lock file, not
data — don't hand-edit it.

## Schema/code cache

`extraction/schema_code_cache.py` (`get_cache()`, backed by
`cache/schema_code_cache.db`) indexes generated Tool 3 code by a hash of the
document's structure (delimiter, header shape, field counts — not row counts
or column names), so two documents of the same shape share one generated
parser instead of paying for a second vLLM call. `get_cache().clear()` resets
it — tests call this to avoid asserting against a stale cache entry from a
previous run.

`cache/column_labels.db` is a separate cache (column-label classification)
with its own lifecycle; both files under `cache/` are runtime data, not
something to hand-edit or diff meaningfully in review.

## Validating Tools 1-4 against real documents

`test_tools_1_to_4.py` fetches real high-PII documents from
`glean.drive_files`, runs Tools 1-4 on each, and validates every tool's
output with `extraction/pipeline_analyzer.py`'s `ToolValidator` /
`PipelineAnalyzer` (status codes, expected fields present, row counts
sane). This is a slower, network- and vLLM-dependent integration check —
distinct from each tool's own `test_tool.py` unit suite under `tools/*/`.

```bash
python test_tools_1_to_4.py
```
