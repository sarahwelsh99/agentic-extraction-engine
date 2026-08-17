# Agentic Extraction Engine

Self-correcting data extraction pipeline: an LLM writes a parser for each
document's own structure, a sandbox runs it, a second pass judges whether it
worked, and a retry gets the failure fed back to the model rather than a
fresh attempt from nothing.

## Architecture overview

Six tools, chained per document by `run_pipeline.py`:

```
1  fetch_and_sample        fetch the source and take a representative sample
2  delimiter_detector      report how the document is laid out
3  generate_parser_script  write a deterministic parser from that report
4  sandbox_execute         run the parser over the whole document
5  evaluate_extraction     decide whether the extraction worked
6  load_to_bigquery        load a passing extraction, one table per guid
```

A failure at step 5 retries from step 3 (bounded by
`MAX_EXTRACTION_ATTEMPTS`), passing the failure back to the model instead of
re-deriving the document's structure from scratch each time.

`population_selection/` runs independently, before any of the above: a
regex pass over `glean.drive_files` that decides which documents contain PII
and are worth extracting at all.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the full module layout,
**[TOOLS.md](TOOLS.md)** for what each tool takes and returns, and
**[PIPELINE_OPERATIONS.md](PIPELINE_OPERATIONS.md)** for running it,
metrics, and caching. **[CLAUDE.md](CLAUDE.md)** has the operating notes and
terminology.

## Key design decisions

### Local LLM first
- Uses a local vLLM server (OpenAI-compatible API) — no external API calls,
  no rate limits, no extraction content leaving the environment.

### Cache generated code, don't regenerate it
- Tool 3's output is cached by a hash of the document's *structure*
  (delimiter, header shape, field count — not row counts or column names),
  so every document sharing a shape reuses one generated parser.

### Reuses mosaic's battle-tested patterns
- Work queue (`extraction/core/workqueue.py`): SQLite bins, LPT packing,
  crash recovery, reused for `run_corpus.py`'s backlog drain.
- BigQuery service layer, retry/backoff for transient failures
  (`extraction/core/bigquery_service.py`).
- Population selection's PII patterns are ported directly from
  `mosaic-glean-extraction`'s production-validated prefilter — see
  [CLAUDE.md](CLAUDE.md).

## Quick Start

### Setup

1. **Prerequisites**
   - 4 NVIDIA L4 GPUs (or equivalent) for the local vLLM server
   - BigQuery credentials (`GOOGLE_APPLICATION_CREDENTIALS` or `gcloud auth`)
   - Python 3.9+

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Start vLLM with tensor parallelism**
   ```bash
   ./scripts/start_vllm.sh
   ```
   See [GPU_SETUP.md](GPU_SETUP.md) for detailed GPU configuration.

4. **Configure**
   ```bash
   cp source.env.example source.env
   # Edit source.env with your project details
   source source.env
   ```

5. **Provision the output table** (once)
   ```bash
   python scripts/provision_extraction_table.py --create
   ```

### Run

```bash
# One document
python run_pipeline.py <guid>

# The whole backlog
python -m population_selection --execute
python run_corpus.py
```

See [PIPELINE_OPERATIONS.md](PIPELINE_OPERATIONS.md) for the full set of
flags, metrics, and caching.

## Configuration

See `extraction/core/config.py` for every option. The ones you're most
likely to touch:

```bash
PROJECT_ID=your-gcp-project
DATASET_ID=glean_extract
GCS_OUTPUT_BUCKET=your-bucket

SOURCE_PROJECT=glean
SOURCE_TABLE=drive_files
SOURCE_TRIAGE_CATEGORY=INCL_STRUCTURED_RECORD

VLLM_API_BASE=http://localhost:8000
VLLM_MODEL=QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8
```

`config.py` also still defines `PHASE1_*`/`PHASE2_*`/`PHASE3_*`/`PHASE4_*`
and `QUEUE_*` variables. The `QUEUE_*` ones are live (used by
`extraction/core/workqueue.py` via `run_corpus.py`); the `PHASE*` ones belong
to the legacy `orchestrator.py` path described in
[ARCHITECTURE.md](ARCHITECTURE.md) and aren't read by anything in the tools
pipeline.

## Testing

```bash
python -m pytest tools/ extraction/ population_selection/
```

Each tool's unit tests live at `tools/<name>/test_tool.py` and run against
the real local vLLM server where a tool calls it — no mocking. Slower,
network-dependent integration checks are separate: `test_tools_1_to_4.py`
(see [PIPELINE_OPERATIONS.md](PIPELINE_OPERATIONS.md)).

## Troubleshooting

### vLLM not responding
```bash
curl http://localhost:8000/v1/models
```

### GPU utilization issues
See [GPU_SETUP.md — Troubleshooting](GPU_SETUP.md#troubleshooting).

### BigQuery authentication
```bash
gcloud auth application-default login
# or set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
```

## References

- [ARCHITECTURE.md](ARCHITECTURE.md) — module layout, legacy vs. live code
- [TOOLS.md](TOOLS.md) — per-tool input/output reference
- [PIPELINE_OPERATIONS.md](PIPELINE_OPERATIONS.md) — running, metrics, caching
- [CLAUDE.md](CLAUDE.md) — operating notes and terminology
- [Mosaic Extraction Reference](https://github.com/sarahwelsh99/mosaic-glean-extraction)
