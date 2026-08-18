# Tools reference

Each tool's own module docstring is the authoritative design doc — this page
is an index: what each tool takes, what it returns, and where its real
documentation lives. See `docs/ARCHITECTURE.md` for how they're chained, and
`extraction/core/pipeline_agent.py` for the state machine that drives Tools
2-5 (Looker/Thinker/Tester/Eval).

| # | Tool | Role | Input | Output |
|---|------|------|-------|--------|
| 1 | `fetch_and_sample` | Looker: Micro-Slicer | `guid` (+ optional `body_text` to skip the fetch) | bounded head+tail sample text, detected format, header position |
| 2 | `structural_inspector` | Looker: Structural Inspector | micro-sliced sample from Tool 1 | looker_spec (header/footer bounds, delimiter, null values) + a derived metadata_report |
| 3 | `generate_parser_script` | Thinker | metadata report + sample (+ `feedback`, `attempt`, optional `session`) | Python `DataExtractor` class |
| 4 | `sandbox_execute` | Tester | generated code + full document + metadata report | extracted rows, execution counts |
| 5 | `evaluate_extraction` | Eval | Tool 4's output + the source document | pass/fail + the numbers behind it |
| 6 | `write_parquet_to_gcs` | Deliver | a passing extraction | one Parquet file per document, guid-partitioned, overwritten on re-extraction |

The regex/heuristic tool `structural_inspector` replaced, and the BigQuery
loader `write_parquet_to_gcs` replaced, are both archived at `retired/` —
see `retired/README.md` for why each was superseded.

## Tool 1 — fetch_and_sample

Reuses `mosaic-glean-extraction`'s batch-fetching logic against
`glean.drive_files` rather than reimplementing it. Two things worth knowing
that aren't obvious from the name:

- **The Micro-Slicer takes a bounded head+tail window**, not a spread sample:
  `MICRO_SLICE_HEAD_LINES` from the top, `MICRO_SLICE_TAIL_LINES` from the
  bottom, capped at `MICRO_SLICE_MAX_BYTES` — the document's complete
  bounding box, since Tool 2's LLM call needs to see both where it starts
  (a title block, comment rows) and where it ends (a footer).
- **`body_text` is accepted directly**, so a caller (tests, `run_pipeline.py`
  with `--body-text`) can skip the BigQuery fetch entirely.

## Tool 2 — structural_inspector

Asks the model to read the document's structure directly from Tool 1's
head+tail slice, as one JSON call (`LocalLLMClient.chat(json_schema=...)`,
strict structured output — no fence-stripping or text recovery needed).
Reports more than the delimiter it replaced could: header row and width, a
footer's location and patterns, this document's own null tokens (`"N/A"`,
`"-"`, ...), and layout anomalies (multi-line rows, inline summaries — logged
but not yet acted on downstream). A document that reaches this tool has
already passed the population-selection PII check, so the only open question
is how to read it, not whether to.

`_to_metadata_report()` derives the flat report shape Tool 3/Tool 4 still
read (`delimiter`, `header_row_index`, `modal_field_count`, ...) from the
richer spec, so neither tool's contract changed. Unlike Tool 3, this call is
not cached: the spec is specific to one document's own header/footer
position, not a reusable shape, so it costs one LLM call per document.

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

## Tool 6 — write_parquet_to_gcs

One Parquet file per document, holding that document's own columns as real
Parquet columns — no shared schema across the corpus and no JSON blob, unlike
the BigQuery table this replaced. Written to a fixed,
guid-partitioned path (`gs://<bucket>/<prefix>/guid=<guid>/part-0000.parquet`)
with no date, so a re-extraction overwrites its predecessor and the output is
exactly-once by construction, with no read-time dedup needed. Every value is
written as a string; the parser's own bookkeeping (`_row_number`, `_valid`,
`_errors`) keeps its underscore prefix and gets real Parquet types.
