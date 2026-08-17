# Pipeline Metrics Recording & Analysis

The orchestration script automatically records metrics to CSV and JSON files (like mosaic-glean-extraction's `throughput.py`), enabling easy analysis and trend tracking.

## Metrics Files

### metrics.csv - Per-Run History

Append-only log of every pipeline run with per-stage timing:

```csv
ts,guid,source,batch_id,tool,duration_sec,rows_extracted,success
2026-08-13T19:25:04.866Z,ddffbdb6-5041-4d65-a744-5a0631a629aa,agentic,1,Tool 1: fetch_and_sample,0.125,3,true
2026-08-13T19:25:04.867Z,ddffbdb6-5041-4d65-a744-5a0631a629aa,agentic,1,Tool 2: infer_schema_and_profile,0.150,3,true
2026-08-13T19:25:11.880Z,ddffbdb6-5041-4d65-a744-5a0631a629aa,agentic,1,Tool 3: generate_parser_script,7.234,3,true
2026-08-13T19:25:15.336Z,ddffbdb6-5041-4d65-a744-5a0631a629aa,agentic,1,Tool 4: sandbox_run_and_evaluate,3.456,3,true
2026-08-13T19:25:16.711Z,ddffbdb6-5041-4d65-a744-5a0631a629aa,agentic,1,Tool 5: write_to_gcs,1.375,3,true
2026-08-13T19:25:16.711Z,ddffbdb6-5041-4d65-a744-5a0631a629aa,agentic,1,pipeline_total,12.34,3,true
```

**Columns:**
- `ts` - ISO 8601 timestamp (UTC)
- `guid` - Document GUID being processed
- `source` - Source name (e.g., "agentic")
- `batch_id` - Batch identifier
- `tool` - Stage name or "pipeline_total"
- `duration_sec` - Time spent (rounded to 3 decimals)
- `rows_extracted` - Rows output (0 for stages, total for pipeline_total)
- `success` - "true" if stage succeeded, "false" otherwise

### metrics.json - Aggregated Per-Source Stats

Running aggregate updated after each run:

```json
{
  "agentic": {
    "runs": 42,
    "total_rows_extracted": 1247,
    "total_duration_sec": 523.4,
    "overall_rate_rows_per_sec": 2.38,
    "last_run": {
      "ts": "2026-08-13T19:25:16.711Z",
      "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
      "batch_id": 1,
      "total_rows": 3,
      "total_duration_sec": 12.34,
      "rate_rows_per_sec": 0.24,
      "success": true
    },
    "updated_at": "2026-08-13T19:25:16.711Z"
  }
}
```

**Fields:**
- `runs` - Total pipeline runs recorded
- `total_rows_extracted` - Cumulative rows extracted across all runs
- `total_duration_sec` - Total time spent (all runs combined)
- `overall_rate_rows_per_sec` - Rows per second (total_rows / total_duration)
- `last_run` - Most recent run details
- `updated_at` - When JSON was last updated

## Analysis Examples

### Using Python (pandas)

```python
import pandas as pd
import json

# Load CSV history
df = pd.read_csv('metrics.csv')

# Per-tool timing analysis
tool_stats = df[df['tool'] != 'pipeline_total'].groupby('tool')['duration_sec'].agg(['count', 'mean', 'min', 'max', 'std'])
print(tool_stats)
#                                          count   mean    min    max   std
# Tool 1: fetch_and_sample                   42  0.125  0.110  0.180 0.018
# Tool 2: infer_schema_and_profile           42  0.150  0.130  0.210 0.025
# Tool 3: generate_parser_script             42  7.234  6.950  8.100 0.320
# Tool 4: sandbox_run_and_evaluate           38  3.456  3.200  4.100 0.310
# Tool 5: write_to_gcs                       38  1.375  1.100  2.050 0.210

# Success rate by tool
success_rate = df.groupby('tool')['success'].value_counts(normalize=True)
print(success_rate)

# Pipeline total throughput trend
pipeline_rows = df[df['tool'] == 'pipeline_total'].sort_values('ts')
print(pipeline_rows[['ts', 'rows_extracted', 'duration_sec']])

# Load JSON for current stats
with open('metrics.json') as f:
    stats = json.load(f)
print(f"Agentic source: {stats['agentic']['runs']} runs, "
      f"{stats['agentic']['overall_rate_rows_per_sec']:.2f} rows/sec")
```

### Using Excel

1. Open `metrics.csv` in Excel
2. Create pivot table: Rows=tool, Values=duration_sec (average)
3. Create line chart: X-axis=ts, Y-axis=duration_sec (by tool)
4. Add conditional formatting to highlight success=false

### Using Command Line

```bash
# Find slowest runs
tail -20 metrics.csv | sort -t, -k6 -rn | head -5

# Count failures
grep 'false$' metrics.csv | wc -l

# Get Tool 3 (vLLM) stats
grep 'generate_parser_script' metrics.csv | awk -F, '{sum+=$6; count++} END {print count" runs, avg "sum/count"s"}'

# Monitor throughput in real-time
tail -f metrics.csv | grep pipeline_total
```

## Optimization Workflow

### 1. Establish Baseline

```bash
# Run multiple documents and collect metrics
for i in {1..10}; do
  python run_pipeline.py guid-$i &
done
wait

# Analyze
python << 'EOF'
import pandas as pd
df = pd.read_csv('metrics.csv')
pipeline_rows = df[df['tool'] == 'pipeline_total']
print(f"Average: {pipeline_rows['duration_sec'].mean():.2f}s")
print(f"P95: {pipeline_rows['duration_sec'].quantile(0.95):.2f}s")
print(f"P99: {pipeline_rows['duration_sec'].quantile(0.99):.2f}s")
EOF
```

### 2. Identify Bottleneck

```python
import pandas as pd
df = pd.read_csv('metrics.csv')
tools_df = df[df['tool'] != 'pipeline_total']
print(tools_df.groupby('tool')['duration_sec'].mean().sort_values(ascending=False))

# Tool 3: generate_parser_script → 7.234s (58% of total)
# Tool 4: sandbox_run_and_evaluate → 3.456s (28% of total)
# Tool 5: write_to_gcs → 1.375s (11% of total)
# Tool 2: infer_schema_and_profile → 0.150s (1% of total)
# Tool 1: fetch_and_sample → 0.125s (1% of total)
```

### 3. Optimize Bottleneck

Target Tool 3 (vLLM) → 7.2s per document

Options:
- Increase vLLM TP (tensor parallelism) from TP-4 to TP-8
- Batch code generation (generate for 5 docs per vLLM call)
- Cache generated code patterns
- Use smaller model variant

### 4. Measure Improvement

Before optimization:
```
Tool 3 avg: 7.234s
Pipeline total avg: 12.34s
```

After optimization:
```
Tool 3 avg: 3.500s (52% improvement)
Pipeline total avg: 8.76s (29% improvement)
```

## Monitoring & Alerting

### Alert on Failures

```bash
#!/bin/bash
# Alert if any failures in last 10 runs
tail -15 metrics.csv | grep 'false$' && \
  echo "Pipeline failures detected!" | mail -s "Alert" ops@example.com
```

### Track Degradation

```bash
#!/bin/bash
# Alert if Tool 3 takes >10s (regression)
latest=$(tail -1 metrics.csv | grep 'generate_parser_script')
duration=$(echo $latest | cut -d, -f6)
if (( $(echo "$duration > 10" | bc -l) )); then
  echo "Tool 3 slow: ${duration}s" | mail -s "Performance Alert" ops@example.com
fi
```

### Dashboard (JSON)

```python
import json
import time

def get_dashboard():
    with open('metrics.json') as f:
        stats = json.load(f)['agentic']
    
    return {
        "status": "running",
        "total_runs": stats['runs'],
        "total_rows": stats['total_rows_extracted'],
        "throughput_rows_per_sec": stats['overall_rate_rows_per_sec'],
        "last_success": stats['last_run']['success'],
        "last_update": stats['updated_at'],
    }

# Expose via HTTP
from flask import Flask, jsonify
app = Flask(__name__)

@app.route('/metrics')
def metrics():
    return jsonify(get_dashboard())
```

## File Locations

**Default paths:**
- `metrics.csv` - Repository root (same level as run_pipeline.py)
- `metrics.json` - Repository root

**Override paths:**
```bash
export METRICS_CSV_FILE="/var/log/pipeline/metrics.csv"
export METRICS_JSON_FILE="/var/log/pipeline/metrics.json"
python run_pipeline.py <guid>
```

## Cross-Process Safety

Like mosaic's throughput.py, metrics recording uses file locking (flock) to safely handle concurrent writes from multiple processes:

- **CSV**: Append-only, each run adds 6-7 rows atomically
- **JSON**: Read-modify-write with atomic rename (temp file → target)
- **Lock file**: `.metrics.lock` serializes all writes

This ensures metrics can be safely recorded from 96 parallel workers without corruption.

## Integration with Monitoring Systems

### Prometheus

```python
# Export metrics to Prometheus
from prometheus_client import Counter, Gauge, Histogram

rows_extracted = Counter('pipeline_rows_extracted_total', 'Total rows extracted')
pipeline_duration = Histogram('pipeline_duration_seconds', 'Pipeline duration')
tool3_duration = Histogram('tool3_duration_seconds', 'Tool 3 duration')

def record_and_export(metrics_dict):
    rows_extracted.inc(metrics_dict['total_rows'])
    pipeline_duration.observe(metrics_dict['duration'])
    tool3_duration.observe(metrics_dict['tool3_duration'])
```

### CloudWatch

```python
import boto3
cloudwatch = boto3.client('cloudwatch')

def send_to_cloudwatch(metrics_dict):
    cloudwatch.put_metric_data(
        Namespace='PipelineMetrics',
        MetricData=[
            {
                'MetricName': 'ThroughputRowsPerSecond',
                'Value': metrics_dict['rate_rows_per_sec'],
                'Unit': 'Count/Second'
            }
        ]
    )
```

## Next Steps

1. **Run pipeline on 10-50 real documents** to build baseline
2. **Analyze metrics.csv** to identify bottleneck (likely Tool 3)
3. **Implement optimization** (vLLM tuning, batching, caching)
4. **Measure improvement** by comparing before/after metrics.json
5. **Set up alerting** for performance regressions
6. **Integrate with monitoring system** (Prometheus, CloudWatch, etc.)

