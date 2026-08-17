# Orchestration Script: End-to-End Pipeline Testing

## Overview

`run_pipeline.py` is the orchestration script that chains Tools 1-5 together for end-to-end testing and optimization analysis. It follows mosaic-glean-extraction's logging patterns for consistency and auditing.

## Features

### 1. Sequential Tool Execution
- Tool 1: fetch_and_sample → fetch document from glean
- Tool 2: infer_schema_and_profile → detect columns and PII
- Tool 3: generate_parser_script → generate extraction code via vLLM
- Tool 4: sandbox_run_and_evaluate → run code in Docker, validate results
- Tool 5: write_to_gcs → write results to GCS as NDJSON

### 2. Comprehensive Logging
- **Console output**: Real-time status (✓ or ✗) for each stage
- **Log file**: Detailed timestamped logs for debugging and optimization
- **JSON output**: Machine-readable results for analysis

### 3. Performance Metrics Collection
- Per-stage duration (milliseconds)
- Per-stage status and metadata
- Total pipeline duration
- Rows extracted
- Error tracking with full context

### 4. Graceful Error Handling
- Stops at first error with detailed context
- Records error stage, message, and timestamp
- Still produces usable metrics for failed stages
- Returns non-zero exit code on failure

## Usage

### Basic Usage

```bash
# Run pipeline on a document GUID
python run_pipeline.py ddffbdb6-5041-4d65-a744-5a0631a629aa

# With test data (for testing without glean access)
python run_pipeline.py <guid> --body-text "Location,Employee ID,Email\nZA - CT,10001,john@test.com"

# With custom log directory
python run_pipeline.py <guid> --log-dir custom_logs

# Save results as JSON
python run_pipeline.py <guid> --json-output results.json
```

### Full Example

```bash
# Test with payroll data
cat > test_payroll.csv << 'EOF'
Location,Employee ID,Legal First Name,Legal Last Name,Email
ZA - Cape Town,10001,John,Smith,john@company.com
ZA - Johannesburg,10002,Jane,Doe,jane@company.com
EOF

python run_pipeline.py "test-guid-123" \
  --body-text "$(cat test_payroll.csv)" \
  --log-dir logs \
  --json-output results.json
```

### Output

```
================================================================================
PIPELINE SUMMARY
================================================================================
GUID: ddffbdb6-5041-4d65-a744-5a0631a629aa
Success: True
Duration: 12.34 seconds
Rows extracted: 2

Stage Timings:
  Tool 1: fetch_and_sample: 0.125s (success)
  Tool 2: infer_schema_and_profile: 0.150s (success)
  Tool 3: generate_parser_script: 7.234s (success)
  Tool 4: sandbox_run_and_evaluate: 3.456s (success)
  Tool 5: write_to_gcs: 1.375s (success)

Full results: logs/pipeline_20260813_192504_ddffbdb6.log
================================================================================
```

## Log Files

### Location
Logs are written to `logs/` directory by default:
```
logs/
  pipeline_20260813_192504_ddffbdb6.log  (timestamp_guid.log)
  pipeline_20260813_193012_3aff74b7.log
  pipeline_20260814_100245_abcdef12.log
```

### Log Format
Each line includes: timestamp, logger name, level, message

```
2026-08-13 19:25:04,866 - __main__ - INFO - Pipeline invoked with guid=...
2026-08-13 19:25:04,866 - tools.base - INFO - Executing tool: fetch_and_sample
2026-08-13 19:25:04,866 - __main__ - INFO - ✓ Tool 1 success: Format=csv, Bytes=256, Sample=3 rows
2026-08-13 19:25:04,867 - tools.base - INFO - Executing tool: infer_schema_and_profile
2026-08-13 19:25:04,867 - __main__ - INFO - ✓ Tool 2 success: Columns=6, PII=4, Rows=3
```

### Log Levels
- **INFO**: Stage progress, success messages
- **ERROR**: Tool failures, exceptions
- **WARNING**: Non-fatal issues, empty batches

## JSON Results Format

When using `--json-output`, results are saved in this format:

```json
{
  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
  "success": true,
  "stages": {
    "tool_1": {
      "status": "success",
      "detected_format_hint": "csv",
      "total_bytes": 256,
      "sample_size": 3
    },
    "tool_2": {
      "status": "success",
      "total_columns": 6,
      "pii_columns": 4,
      "total_rows": 3
    },
    "tool_3": {
      "status": "success",
      "code_length": 3717,
      "syntax_valid": true,
      "pii_columns": 4
    },
    "tool_4": {
      "status": "success",
      "total_rows": 3,
      "successful_rows": 3,
      "success_rate": "100.0%",
      "validation_status": "success",
      "validation_method": "fast_path_auto_pass"
    },
    "tool_5": {
      "status": "success",
      "rows_written": 3,
      "bytes_written": 487,
      "uri": "gs://bucket/extraction-artifacts/..."
    }
  },
  "metrics": {
    "start_time": "2026-08-13T19:25:04...",
    "end_time": "2026-08-13T19:25:16...",
    "total_duration_sec": 12.34,
    "total_rows_extracted": 3,
    "error_count": 0,
    "stages": {
      "Tool 1: fetch_and_sample": {
        "duration_sec": 0.125,
        "status": "success",
        "format": "csv",
        "total_bytes": 256,
        "sample_size": 3
      },
      ...
    }
  }
}
```

## Metrics Collection

The `PipelineMetrics` class automatically collects:

### Per-Stage Metrics
```python
{
  "Tool 1: fetch_and_sample": {
    "duration_sec": 0.125,
    "status": "success",
    "format": "csv",
    "total_bytes": 256,
    "sample_size": 3,
    "timestamp": "2026-08-13T19:25:04..."
  },
  ...
}
```

### Pipeline-Level Metrics
- `start_time`: When pipeline started (ISO 8601)
- `end_time`: When pipeline ended
- `total_duration_sec`: Total execution time in seconds
- `total_rows_extracted`: Number of rows output from Tool 4
- `error_count`: Number of errors encountered

## Optimization Analysis

Use the metrics to identify bottlenecks:

```bash
# Parse results to find slowest stage
cat results.json | jq '.metrics.stages | to_entries | sort_by(-.value.duration_sec) | .[0]'

# Compare multiple runs to establish baseline
for i in {1..5}; do
  python run_pipeline.py <guid> --json-output results_$i.json
done

# Analyze per-stage variance
for i in {1..5}; do
  cat results_$i.json | jq '.metrics.stages."Tool 3: generate_parser_script".duration_sec'
done
```

## Troubleshooting

### Tool 3 (vLLM) Fails
```
Error: Failed to generate code from vLLM
```
**Solution**: Start vLLM server
```bash
python -m vllm.entrypoints.openai.api_server \
  --model QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8 \
  --tensor-parallel-size 4 \
  --port 8000
```

### Tool 5 (GCS) Fails
```
Error: No output bucket configured
```
**Solution**: Set environment variable
```bash
export GCS_OUTPUT_BUCKET="my-bucket"
export GCS_ARTIFACTS_PREFIX="extraction-artifacts"
python run_pipeline.py <guid>
```

### Tool 4 (Docker) Fails
```
Error: Docker execution failed
```
**Solution**: Ensure Docker is running
```bash
docker ps  # Check if Docker daemon is running
```

## Integration with CI/CD

### GitHub Actions Example
```yaml
- name: Test pipeline
  run: |
    python run_pipeline.py $TEST_GUID \
      --body-text "$TEST_CSV" \
      --json-output results.json
    
- name: Check success rate
  run: |
    cat results.json | jq -e '.metrics.stages."Tool 4: sandbox_run_and_evaluate".success_rate > 0.95'
```

### Monitoring Setup
```bash
#!/bin/bash
# Run pipeline, parse metrics, alert on failures

python run_pipeline.py $GUID --json-output results.json

SUCCESS=$(jq '.success' results.json)
DURATION=$(jq '.metrics.total_duration_sec' results.json)

if [ "$SUCCESS" != "true" ]; then
  echo "Pipeline failed!" | mail -s "Pipeline Alert" ops@example.com
fi

if (( $(echo "$DURATION > 30" | bc -l) )); then
  echo "Pipeline took ${DURATION}s (slow)" | mail -s "Performance Alert" ops@example.com
fi
```

## Key Metrics to Track

### Performance
- **Per-stage duration**: Identify bottlenecks
- **Total duration**: Track optimization progress
- **Throughput**: Rows/second extracted

### Quality
- **Success rate** (Tool 4): % of rows extracted successfully
- **Validation method** (Tool 4): fast-path vs LLM judgment
- **Error count**: Non-zero means data issues

### Resource Usage
- **Code length** (Tool 3): LLM output quality
- **Bytes written** (Tool 5): Compression ratio
- **GCS URI**: Confirm writes to correct location

## Next Steps

After validating end-to-end pipeline:

1. **Run on multiple documents** to get statistical performance data
2. **Identify bottleneck** from metrics (usually Tool 3 with vLLM)
3. **Optimize** the slowest stage
4. **Batch process** multiple documents in parallel with 96 workers
5. **Build Tool 6** (load_gcs_to_bigquery) for final BigQuery step

