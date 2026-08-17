# Operating Notes for Agentic Extraction Pipeline

## Key Concepts

### Four Phases (Not Conflatable)

- **Phase 1**: Pattern analysis + code generation (LLM-driven, hours)
- **Phase 2**: Safety validation + testing (deterministic, 30 min)
- **Phase 3**: Quality feedback loop (LLM-driven, iterative)
- **Phase 4**: Deterministic execution at scale (no LLMs, hours-days)

Running "Phase 1" means the entire Phase 1 workflow (sample fetching → analysis → code generation).

### Data Source Terminology

All data comes from `glean.drive_files` filtered by:
```sql
WHERE triage_category = 'INCL_STRUCTURED_RECORD'
```

This is a **single, fixed source** (unlike mosaic's multiple datasources).

### Extraction vs. Inference vs. Code Generation

- **Extraction**: Taking data from a document (what the pipeline does)
- **Inference**: Running neural net forward pass (vLLM in Phases 1-3)
- **Code generation**: Using LLM to synthesize Python code (Phase 1)
- **Deterministic code execution**: Running generated code on data (Phase 4, no LLM)

## Operating Patterns

### Phase 1: Code Generation
- **Upfront**: Fetch 20 random samples from BigQuery
- **Analysis**: Send to vLLM, ask "what patterns do you see?"
- **Code Gen**: Send patterns to vLLM, ask "write Python to extract these"
- **Output**: `extractors_v<N>.py` (versioned in GCS artifacts)

Never run Phase 1 manually. Always via `orchestrator.py --phase 1`.

### Phase 2: Safety + Testing
- **Deterministic only**: No vLLM calls
- **AST inspection**: Parse Python code, check for dangerous patterns
- **Test execution**: Run generated code on sample data, verify output structure
- **Decision**: APPROVED or REJECTED

Once Phase 2 passes, code is locked and signed (checksum recorded).

### Phase 3: Quality Loop (Not Yet Implemented)
- **Sample execution**: Run Phase 2-approved code on 10K random payloads
- **Quality eval**: Send sample results to vLLM for quality grading
- **Decision**:
  - If quality ≥ 85%: Lock version, proceed to Phase 4
  - If quality < 85%: Feed failure patterns back to Phase 1, re-analyze (max 3 iterations)

### Phase 4: Scale Execution
- **Upfront**: Single BigQuery read of metadata (guid + body_length)
- **Build queue**: Local SQLite work queue, LPT bin-packing
- **During execution**:
  - Prefetcher thread: Fetch next bin's body_text async (from BQ)
  - Worker threads: Run extractors (no BQ calls)
  - Writer thread: Flush to GCS + status ledger async (no BQ writes)
- **Zero BigQuery writes**: Status goes to GCS ledger (reconciled by cron later)
- **Post-execution**: Separate `load_extracted_to_bq.py` cron loads results (every 4h)

Never call Phase 4 manually while other phases are running.

## Population Selection (Runs Before Phase 1)

`population_selection/` is a standalone module — it doesn't import
orchestrator.py, run_pipeline.py, phase1-4, or tools/ — that decides which
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
any row an earlier version of this module left there) — anything Phase 4 has
already completed or errored on is left untouched, so tuning the regex
patterns or re-running after new source rows land is always safe.

## Status Table Schema

The `pii_extraction_status` table tracks population selection and extraction progress:

| Column | Type | Meaning |
|---|---|---|
| `guid` | STRING | Document ID |
| `status` | STRING | `excluded_no_pii` \| `pending` \| `complete` \| `error_*` \| `dense` \| `oversized` |
| `extraction_version` | STRING | Version of code that processed this (e.g., `v1.0`) |
| `extracted_at` | TIMESTAMP | When extraction completed |
| `error_message` | STRING | Error details if status is `error_*` |
| `body_length` | INTEGER | Size of input document |
| `body_text` | STRING | The actual document text |
| `pii_score` | INTEGER | Count of PII categories matched (population selection) |
| `pii_signals` | STRING | Comma-joined category names matched, e.g. `DOB,ADDRESS` |
| `pii_detection_method` | STRING | `regex` (population selection's only detection method) |

### Status Values

- `excluded_no_pii` — population selection's regex pass found no PII category signal; will not be extracted
- `pending` — flagged as containing PII, not yet processed by extraction
- `complete` — successfully extracted
- `error_llm` — vLLM call failed
- `error_truncated` — output was cut off at token cap
- `error_oversized` — document exceeds max size, skipped entirely
- `dense` — document classified as structured (not prose), skipped
- `no_body` — document had no extractable text

## GCS Output Structure

```
gs://extraction-output/
  source=drive/
    dt=2026-08-12/
      run=abc123def456/
        batch-000001-part-00001.jsonl  # Extracted results
        batch-000001-part-00002.jsonl
        ...

gs://extraction-artifacts/
  source=drive/
    dt=2026-08-12/
      schema_v1.json         # Target schema
      samples_v1.jsonl       # Input samples
      analysis_v1.json       # Pattern analysis
      extractors_v1.py       # Generated code
      extractors_v1.hash     # SHA256 of code
      metadata.json          # Gen timestamp, model, etc.

gs://extraction-status-ledger/
  source=drive/
    dt=2026-08-12/
      run=abc123def456/
        ledger-000001-part-00001.jsonl  # Status updates
        ledger-000001-part-00002.jsonl
        ...
```

## Code Generation Guarantees

Generated code (Phase 1) is:
- **Deterministic**: Same input → same output, always
- **Stateless**: Pure functions, no side effects
- **Safe**: Validated by Phase 2 AST inspection
- **Testable**: Runs on samples before Phase 4

Generated code is NOT:
- Optimized for speed (correctness first)
- Handling every edge case (only documented ones)
- A replacement for manual review (always inspect before trusting)

## When Phase 3 Feeds Back to Phase 1

Phase 3 (quality loop) can trigger Phase 1 re-analysis if:
- Extraction quality < 85% threshold
- Specific field types missing (e.g., "no phone numbers extracted")
- Systematic errors (e.g., "extracting email from wrong fields")

When Phase 1 re-runs:
- Same samples are analyzed again
- vLLM is told "your previous code missed X, Y, Z — fix it"
- New code is generated (v1.1, v1.2, etc.)
- Phase 2 re-validates
- Phase 3 re-samples and re-evaluates
- Max 3 iterations before human review required

## Local vLLM Configuration

- **Model**: `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8` (30B params, code-tuned)
- **Endpoint**: `http://localhost:8000` (OpenAI-compatible API)
- **Timeout**: 300s per request
- **Temperature**: 0.0 (deterministic generation)
- **Max tokens**: 2000-4000 depending on phase

If vLLM is unavailable:
```bash
# Check status
curl http://localhost:8000/v1/models

# Or check process
ps aux | grep vllm

# Or check logs (depends on how vLLM was started)
```

## Running Phases in Parallel

- ❌ Never run Phase 1 and Phase 4 in parallel (Phase 4 depends on Phase 1's output)
- ✅ Can run Phase 2 on different machines (testing is local, no shared state)
- ✅ Phase 3 and Phase 4 must be sequential (Phase 3 feeds back to Phase 1)

In practice: Run `orchestrator.py --phase 1-4` once. It's a pipeline, not a DAG.

## Debug Flags

```bash
# Verbose logging
LOG_LEVEL=DEBUG python orchestrator.py

# Dry-run (validate config, don't execute)
python orchestrator.py --dry-run

# Just Phase 2 safety checks (no execution)
python orchestrator.py --phase 2

# Phase 4 with smaller bins (faster local testing)
QUEUE_TARGET_BIN_GUIDS=100 python orchestrator.py --phase 4
```

## Metrics & Monitoring

Each phase produces:
- **Phase 1**: `analysis_v<N>.json` (patterns found)
- **Phase 2**: `safety_report_v<N>.json` (violations, test results)
- **Phase 3**: `quality_eval_v<N>.json` (pass rate, failure categories)
- **Phase 4**: `extraction_run_<id>.log` (throughput, error distribution)

Check GCS artifacts and logs for details.

## Cost Model

| Phase | Cost | LLM Calls |
|---|---|---|
| Phase 1 | ~$0.50 | 20-30 (samples + code gen) |
| Phase 2 | ~$0.00 | 0 (all deterministic) |
| Phase 3 | ~$1.00 | 50-100 (quality eval) |
| Phase 4 | ~$0.00 | 0 (pure code execution) |
| **Total (1M docs)** | **~$2-3** | **100-130** |

Compare: Traditional LLM extraction = $500K+ for 1M docs (1000 docs × $0.50/doc).

## Reused Patterns from Mosaic

This pipeline reuses these **battle-tested** patterns from mosaic-glean-extraction:

1. **Work queue** (workqueue.py): SQLite bins, LPT packing, crash recovery
2. **Status ledger** (status_ledger.py): GCS NDJSON, deferred BQ reconciliation
3. **Output store** (output_store.py): GCS NDJSON with Hive-style partitioning
4. **Async writer + prefetcher**: Non-blocking I/O, hidden latency
5. **Retry logic** (retry_bq): Exponential backoff, transient vs. permanent errors
6. **Config pattern**: Env-driven, profile-based
7. **BigQuery service layer**: Connection pooling, schema management

These are all in `extraction/` and used by Phase 4 executor.

## Common Mistakes

### Mistake 1: Running Phase 1 with live data
❌ Wrong:
```bash
python -c "from phase1.analyzer import analyze_samples; ..."
```

✅ Right:
```bash
python orchestrator.py --phase 1
```

Reason: Phase 1 should be repeatable and deterministic. Always via orchestrator.

### Mistake 2: Calling Phase 4 without Phase 1-3
❌ Wrong: Phase 4 executor depends on `extractors_v<N>.py` from Phase 1

✅ Right: Always run `orchestrator.py --phase 1-4` (or separately in sequence)

### Mistake 3: BigQuery reads during Phase 4 execution
❌ Assumption: "Phase 4 has zero BQ calls"
✅ Reality: Phase 4 has zero BQ **writes**. Reads are async (prefetcher).

The status ledger pattern allows status updates to batch in GCS, then load to BQ separately.

### Mistake 4: Changing schema during Phase 4
❌ Wrong: Modifying `config.SCHEMA_FIELDS` mid-execution

✅ Right: Schema is locked at Phase 1 time. New schema = new v2.0 pipeline.

## Operational Checklist

Before running Phase 1:
- [ ] vLLM server running and responding to `curl http://localhost:8000/v1/models`
- [ ] BigQuery credentials configured (`GOOGLE_APPLICATION_CREDENTIALS` or `gcloud auth`)
- [ ] Config values set (`PROJECT_ID`, `GCS_OUTPUT_BUCKET`, etc.)
- [ ] Status table exists (created by orchestrator on first run)

Before running Phase 4:
- [ ] Phases 1-3 completed successfully
- [ ] `extractors_v<N>.py` exists in GCS artifacts
- [ ] BigQuery metadata read passes (query drive_files works)
- [ ] GCS bucket has write permissions
- [ ] Enough local disk for work queue SQLite (typically <100 MB)
- [ ] `python -m population_selection` has run (Phase 4 should only see rows population selection flagged `pending`)

## Questions?

See README.md for quick start, or extraction/docs/ARCHITECTURE.md for detailed design.
