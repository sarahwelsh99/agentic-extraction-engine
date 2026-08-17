# Tools reference

Each tool's own module docstring is the authoritative design doc — this page
is an index: what each tool takes, what it returns, and where its real
documentation lives. See `docs/ARCHITECTURE.md` for how they're chained.

| # | Tool | Input | Output |
|---|------|-------|--------|
| 1 | `fetch_and_sample` | `guid` (+ optional `body_text` to skip the fetch) | raw sample text, detected format, header position |
| 2 | `delimiter_detector` | raw sample from Tool 1 | metadata report: delimiter, header row/width, sheet size |
| 3 | `generate_parser_script` | metadata report + sample (+ `feedback`, `attempt`, optional `session`) | Python `DataExtractor` class |
| 4 | `sandbox_execute` | generated code + full document + metadata report | extracted rows, execution counts |
| 5 | `evaluate_extraction` | Tool 4's output + the source document | pass/fail + the numbers behind it |
| 6 | `load_to_bigquery` | a passing extraction | rows loaded into one shared BigQuery table (JSON column carries the document's own shape) |

An unwired alternative to Tool 6, `write_parquet_to_gcs`, writes one Parquet
file per document instead — see its own docstring for why (no shared schema
to force into a BigQuery column, exactly-once by path instead of by dedup
query). It's not in `tools/__init__.py`'s registry and `run_pipeline.py`
doesn't call it.

## Tool 1 — fetch_and_sample

Reuses `mosaic-glean-extraction`'s batch-fetching logic against
`glean.drive_files` rather than reimplementing it. Two things worth knowing
that aren't obvious from the name:

- **Header detection is not fixed to row 0.** It scans for the header at
  whatever row position it actually sits at — real documents carry version
  banners or comment rows above the header.
- **`body_text` is accepted directly**, so a caller (tests, `run_pipeline.py`
  with `--body-text`) can skip the BigQuery fetch entirely.

## Tool 2 — delimiter_detector

Pure structure detection — delimiter, header row/width, sheet size — with no
opinion on what the columns *mean*. That's deliberate: a document reaching
this tool has already passed the population-selection PII check, so the only
open question is how to read it, not whether to.

## Tool 3 — generate_parser_script

Turns Tool 2's report into a parser via vLLM, caching generated code by
schema hash (`extraction/schema_code_cache.py`) so two documents with the same
shape never pay for a second generation. On a cache miss, and when the caller
passes an `LLMSession` (see `docs/ARCHITECTURE.md` and
`extraction/core/llm_service.py`), a retry after a failure is a short
"fix this" turn against the code and error the model already saw, not a
rebuilt from-scratch prompt.

The generated class returns column values by *position*, never by name:
naming a column is `sandbox_execute`'s job against Tool 2's report, which is
what lets one cached parser serve any document of the same shape regardless
of what its columns are called.

## Tool 4 — sandbox_execute

Runs the model-written, unreviewed code from Tool 3 in a Docker container
with no network, a read-only filesystem, bounded CPU/memory, and an
unprivileged user. Reports what came out and nothing about whether it's
*right* — that judgment is Tool 5's, kept in one place on purpose.

## Tool 5 — evaluate_extraction

One binary question, three ways to fail it, all measured against the source
document rather than the extraction in isolation: the script didn't run, too
few rows parsed, or far fewer records came out than the source had. Writes
nothing — a passing extraction is handed to Tool 6 by the caller
(`run_pipeline.py`).

## Tool 6 — load_to_bigquery

One shared table for every document (not one table per document) because
BigQuery's load quota counts jobs, not rows — a table per document would mean
a job per document, the binding constraint at corpus scale. Each document's
own columns ride along in a JSON column. Loads append rather than overwrite;
read the latest generation with the `QUALIFY ROW_NUMBER() ... ORDER BY
extracted_at DESC` pattern documented in the tool's own docstring, or call
`delete_document()` first when reprocessing one document by hand.
