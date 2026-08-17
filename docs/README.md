# Agentic Extraction Engine

High-throughput, self-correcting data extraction pipeline using LLMs as logic generators and quality judges.

## Architecture Overview

The pipeline has four distinct phases:

### Phase 1: Pattern Analysis & Code Generation
- Analyzes sample payloads (5-20 documents)
- Uses local vLLM to identify structural patterns
- Generates deterministic Python extraction code
- Output: `extractors_v<N>.py` with standard interface

### Phase 2: Safety Validation & Testing
- AST-based safety inspection (no dangerous imports/calls)
- Deterministic test execution on sample data
- Schema compliance validation
- Output: Approved, signed extractors

### Phase 3: Quality Feedback Loop
- Sample execution on 10K payloads
- LLM-driven quality evaluation
- Identifies failure patterns
- Triggers re-analysis if quality is below threshold

### Phase 4: Deterministic Execution at Scale
- Executes validated extractors on millions of payloads
- Local SQLite work queue (crash-safe, resumable)
- Background prefetcher for async BigQuery reads
- Async writer to GCS (zero BigQuery writes during execution)
- Uses status ledger pattern (separate cron for BQ reconciliation)

## Key Design Decisions

### Local LLM First
- Uses local vLLM server (OpenAI-compatible API)
- No external API calls or rate limits
- Full privacy for extraction patterns

### LLM for Code Generation, Not Direct Inference
- **Expensive (Phases 1-3)**: LLM analysis, code generation, quality evaluation
- **Cheap (Phase 4)**: Deterministic code execution at CPU/database speed
- **Cost model**: ~$2-5 LLM cost for 1M payloads (front-loaded), then $0 per payload

### Reuses Mosaic's Battle-Tested Patterns
- Work queue (SQLite bins, LPT packing, crash recovery)
- Status ledger (GCS NDJSON, deferred BigQuery reconciliation)
- Async writer and prefetcher threads
- Retry/backoff for transient failures

## Quick Start

### Setup

1. **Prerequisites**
   - 4 NVIDIA L4 GPUs (or equivalent)
   - vLLM with tensor parallelism
   - BigQuery credentials (`GOOGLE_APPLICATION_CREDENTIALS`)
   - Python 3.9+

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Start vLLM with tensor parallelism** (NEW!)
   ```bash
   chmod +x start_vllm.sh
   ./start_vllm.sh
   # Automatically enables --tensor-parallel-size 4 across all GPUs
   # Displays GPU activation status
   ```
   See [GPU_SETUP.md](extraction/docs/GPU_SETUP.md) for detailed GPU configuration.

4. **Configure pipeline**
   ```bash
   cp source.env.example source.env
   # Edit source.env with your project details
   source source.env
   ```

5. **Initialize status table**
   ```bash
   python -c "from extraction import bigquery_service, config; \
             client = bigquery_service.get_bigquery_client(); \
             bigquery_service.initialize_status_table(client, \
               bigquery_service.get_status_table_id(client))"
   ```

### Run Pipeline

```bash
# Run all phases (1-4)
python orchestrator.py

# Run specific phases
python orchestrator.py --phase 1      # Pattern analysis only
python orchestrator.py --phase 1-2    # Through safety validation
python orchestrator.py --phase 4      # Scale execution (requires Phase 1-3 complete)

# Dry-run (validate config without executing)
python orchestrator.py --dry-run

# Resume Phase 4 from checkpoint
python orchestrator.py --phase 4 --resume
```

## Configuration

See `extraction/config.py` for all configuration options. Key env variables:

```bash
# Project & Infrastructure
PROJECT_ID=your-gcp-project
GCS_OUTPUT_BUCKET=your-bucket
DATASET_ID=glean_extract

# Data Source
SOURCE_PROJECT=glean
SOURCE_TABLE=drive_files
SOURCE_TRIAGE_CATEGORY=INCL_STRUCTURED_RECORD

# Local vLLM
VLLM_API_BASE=http://localhost:8000
VLLM_MODEL=QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8

# Phase Configuration
PHASE1_SAMPLES_PER_SOURCE=20
PHASE2_SAFETY_CHECK_ENABLED=true
PHASE3_QUALITY_SAMPLE_SIZE=10000
PHASE4_MAX_WORKERS=96
```

## Data Flow

```
glean.drive_files (INCL_STRUCTURED_RECORD)
        ↓
  [Phase 1: Code Gen]
        ↓
  [Phase 2: Safety]
        ↓
  [Phase 3: Quality Loop]
        ↓
  [Phase 4: Execute]
        ↓
  GCS Output
        ↓
  load_extracted_to_bq.py (cron, 4h)
        ↓
  BigQuery pii_extraction table
```

## File Structure

```
extraction/
  phase1/
    analyzer.py          # Pattern analysis
    code_generator.py    # Code synthesis
  phase2/
    code_validator.py    # AST safety checks
    test_runner.py       # Test execution
  phase3/               # Quality loop (scaffolding)
  phase4/               # Scale execution (scaffolding)
  
  # Core Infrastructure (from mosaic)
  config.py             # Configuration
  bigquery_service.py   # BQ operations
  llm_service.py        # Local vLLM client
  workqueue.py          # SQLite work queue
  status_ledger.py      # GCS status ledger
  output_store.py       # GCS output writer
  throughput.py         # Metrics

orchestrator.py         # Main entry point
```

## Development

### Adding New Phases
1. Create `extraction/phase<N>/` directory
2. Implement phase logic
3. Add entry point in `orchestrator.py`

### Testing
```bash
python -m pytest extraction/tests/
```

### Local Development
```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run with debug logging
DEBUG=true LOG_LEVEL=DEBUG python orchestrator.py --phase 1-2
```

## Performance

Expected throughput (Phase 4):
- GPU vLLM: ~5-10 docs/sec (concurrent LLM inference)
- CPU extraction: ~100-1000 docs/sec (deterministic code)
- BigQuery: Prefetcher hides latency (async background fetches)
- GCS writes: 1000+ docs/sec (batched, async)

## Monitoring

Each phase produces:
- **Phase 1**: Pattern analysis JSON, generated code, code hash
- **Phase 2**: Safety report, test results, schema validation
- **Phase 3**: Quality metrics, failure patterns, iteration count
- **Phase 4**: Extraction rate, error distribution, throughput metrics

Check logs and GCS artifacts for detailed progress.

## GPU & Tensor Parallelism

### Verify All GPUs Are Working

After starting vLLM with `./start_vllm.sh`, check:

```bash
# 1. vLLM startup log shows all GPUs active
#    Look for: "✓ ACTIVE" for all 4 GPUs

# 2. Real-time monitoring during pipeline
python orchestrator.py --phase 1
# Displays GPU status each minute:
#   GPU 0: 45% util | Memory: 50% | Temp: 45°C | ✓ ACTIVE
#   GPU 1: 42% util | Memory: 51% | Temp: 44°C | ✓ ACTIVE
#   GPU 2: 44% util | Memory: 55% | Temp: 46°C | ✓ ACTIVE
#   GPU 3: 41% util | Memory: 53% | Temp: 45°C | ✓ ACTIVE

# 3. Manual verification
watch -n 1 nvidia-smi
# All 4 GPUs should show >0% GPU-Util and similar memory
```

See [GPU_SETUP.md](extraction/docs/GPU_SETUP.md) for:
- Detailed tensor parallelism configuration
- Troubleshooting unbalanced GPU utilization
- Performance expectations with TP-4
- Manual GPU monitoring scripts

### Expected Performance

With tensor parallelism across 4 L4 GPUs:
- **Phase 1 (Code Gen)**: ~2-5 docs/sec
- **Phase 3 (Quality)**: ~2-5 docs/sec
- **Phase 4 (Execute)**: 100-1000+ docs/sec (CPU-only, no GPU)
- **All 4 GPUs**: Active and balanced during Phases 1-3

## Troubleshooting

### vLLM not responding
```bash
curl http://localhost:8000/v1/models
# Should return list of available models
```

### GPU utilization issues
See [GPU_SETUP.md - Troubleshooting](extraction/docs/GPU_SETUP.md#troubleshooting) for:
- Only 1 GPU active (enable tensor parallelism)
- CUDA out of memory (lower memory utilization)
- Imbalanced GPU usage (layer distribution issues)

### BigQuery authentication
```bash
gcloud auth application-default login
# Or set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
```

### Schema validation failures
Check `SCHEMA_FIELDS` in `config.py` matches your target schema.

## References

- [Architecture Design](extraction/docs/ARCHITECTURE.md)
- [Mosaic Extraction Reference](https://github.com/sarahwelsh99/mosaic-glean-extraction)
