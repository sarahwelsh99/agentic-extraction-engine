# Pipeline Analyzer - Usage Guide

Comprehensive analyzer for Tools 1-4 that validates output, identifies inaccuracies, and generates detailed reports with GUID tracking for each document.

## Quick Start

```bash
python test_tools_1_to_4.py
```

This will:
1. Fetch 50 real documents from glean with high PII content
2. Run Tools 1-4 on each document
3. Validate outputs and identify issues
4. Generate comprehensive analysis reports

**Duration**: ~5-10 minutes for 50 documents

## Output Files

After running the analyzer, check these files in the `analysis/` directory:

### 1. `analysis_results.json` (Most Important)
Complete analysis results including:
- Overall summary statistics
- Per-document results with GUID
- Issues grouped by tool
- Success rates

**Key fields for each document:**
```json
{
  "guid": "3aff74b7-1f1d-f5ae-e177-779175d64819",
  "title": "Employee Payroll Record",
  "overall_status": "success|failed",
  "tools": {
    "tool1": {
      "status": "success|error",
      "issues": ["Tool 1: Format not detected", ...]
    },
    "tool2": {...},
    "tool3": {...},
    "tool4": {...}
  }
}
```

### 2. `document_details.csv`
Quick-reference table showing:
- GUID
- Document title and type
- Overall status for each document
- Summary of issues (first 2 per tool)

**Open in Excel to sort/filter by:**
- Status (success/failed)
- Tool (which tools are failing)
- Document type (CSV, JSON, etc.)

### 3. `full_outputs.jsonl`
Raw outputs from all tools for each document (one JSON object per line).

**Use for:**
- Debugging specific documents
- Detailed analysis of extraction results
- Understanding exact error messages

### 4. `analysis_report.html`
Interactive HTML report with:
- Summary statistics (success rate, total issues)
- Tool-by-tool success rates
- List of failed documents with issues
- Easy to share with team

**Open in browser:** `open analysis/analysis_report.html`

## Analyzing Results

### Find documents with issues for a specific tool

```bash
# Tool 1 failures (format detection, sampling)
grep -l "Tool 1:" analysis/*.json | head -5

# Tool 2 failures (schema inference)
grep "Tool 2:" analysis/analysis_results.json | head -10

# Tool 3 failures (code generation)
grep "Tool 3:" analysis/analysis_results.json | head -10

# Tool 4 failures (extraction/validation)
grep "Tool 4:" analysis/analysis_results.json | head -10
```

### Python analysis

```python
import json

with open("analysis/analysis_results.json") as f:
    results = json.load(f)

# Get failed documents
failed = [
    (guid, doc["title"], doc["tools"])
    for guid, doc in results["documents"].items()
    if doc["overall_status"] == "failed"
]

print(f"Failed documents: {len(failed)}")
for guid, title, tools in failed[:5]:
    print(f"\n  GUID: {guid}")
    print(f"  Title: {title}")
    for tool_name, tool_result in tools.items():
        if tool_result["issues"]:
            print(f"    {tool_name}: {tool_result['issues'][0]}")

# Get success rate by tool
for tool_name in ["tool1", "tool2", "tool3", "tool4"]:
    summary = results["summary"][tool_name]
    total = summary["success"] + summary["failed"]
    rate = summary["success"] / total * 100 if total > 0 else 0
    print(f"{tool_name}: {rate:.1f}%")
```

## Understanding Issues

### Tool 1 Issues (fetch_and_sample)

| Issue | What it means | Fix |
|-------|--------------|-----|
| Format not detected | Couldn't identify CSV/JSON | Document might be malformed |
| Sample too small | Sample data < 10 chars | Document is too short |
| Sample size not recorded | Missing metadata | Bug in Tool 1 |
| Header row not identified | Couldn't find column headers | CSV might not have headers |

### Tool 2 Issues (infer_schema_and_profile)

| Issue | What it means | Fix |
|-------|--------------|-----|
| No columns identified | Schema inference failed | Data format might be unusual |
| Invalid column type | Type not in {string, integer, float, boolean, date} | Type detection bug |
| Column type unreasonable | Type doesn't match values (e.g., "John" as integer) | Improve type inference logic |
| Nullable flag missing | Missing null-safety metadata | Bug in Tool 2 |

### Tool 3 Issues (generate_parser_script)

| Issue | What it means | Fix |
|-------|--------------|-----|
| Generated code too short | Code < 50 chars | vLLM didn't generate proper code |
| Syntax errors | Generated code won't compile | Improve prompt or post-process |
| Missing DataExtractor class | Code doesn't have required structure | vLLM format issue |
| Missing parse_row method | Core extraction method missing | Prompt doesn't specify correctly |
| Missing _valid/_errors fields | Output structure incomplete | vLLM not following spec |
| Missing type hints | Code quality issue | vLLM not including typing |
| Missing error handling | Code has no try/except | vLLM missing requirements |

### Tool 4 Issues (sandbox_run_and_evaluate)

| Issue | What it means | Fix |
|-------|--------------|-----|
| No rows extracted | Docker didn't extract anything | Generated code doesn't work |
| Invalid success rate | Rate not in [0, 1] | Bug in metrics calculation |
| Low success rate | Success rate < 70% | Generated code/data mismatch |
| Invalid validation result | Validation state unexpected | Bug in Tool 4 |

## Example: Debug a Specific Document

**Task**: Document `abc123def456` failed. What went wrong?

```python
import json

# Load full outputs for that document
with open("analysis/full_outputs.jsonl") as f:
    for line in f:
        doc = json.loads(line)
        if doc["guid"].startswith("abc123def456"):
            # Examine each tool's output
            tool1 = doc["outputs"]["tool1"]
            tool2 = doc["outputs"]["tool2"]
            tool3 = doc["outputs"]["tool3"]
            tool4 = doc["outputs"]["tool4"]

            # Find which tool first failed
            if tool1.get("status") != "success":
                print(f"Tool 1 failed: {tool1.get('error')}")
            elif tool2.get("status") != "success":
                print(f"Tool 2 failed: {tool2.get('error')}")
                print(f"Tool 2 detected {len(tool2['columns'])} columns")
                for col in tool2["columns"]:
                    print(f"  - {col['name']}: {col['detected_type']}")
            elif tool3.get("status") != "success":
                print(f"Tool 3 failed: {tool3.get('error')}")
                print(f"Generated code length: {len(tool3.get('generated_code', {}).get('code', ''))}")
            elif tool4.get("status") != "success":
                print(f"Tool 4 failed: {tool4.get('error')}")
                print(f"Success rate: {tool4['quality_metrics']['success_rate']:.1%}")

            break
```

## Validation Rules

The analyzer checks:

**Tool 1:**
- ✓ Status is "success"
- ✓ Format detected (csv/json)
- ✓ Sample has content (>10 chars)
- ✓ Header row identified

**Tool 2:**
- ✓ Status is "success"
- ✓ Columns identified (>0)
- ✓ Schema populated
- ✓ Column types valid
- ✓ Nullable flags set

**Tool 3:**
- ✓ Status is "success"
- ✓ Code generated (>50 chars)
- ✓ Syntax valid
- ✓ Has DataExtractor class
- ✓ Has parse_row method
- ✓ Has _valid/_errors structure
- ✓ Has type hints
- ✓ Has error handling

**Tool 4:**
- ✓ Status is "success"
- ✓ Rows extracted (>0)
- ✓ Valid success rate
- ✓ Success rate >= 70% (warning if lower)
- ✓ Valid validation result

## Customizing the Analyzer

### Modify validation rules

Edit `extraction/pipeline_analyzer.py`:

```python
def validate_tool1(self, guid: str, output: Dict[str, Any]) -> List[str]:
    issues = []
    
    # Add custom check
    sample_size = output.get("sample_size", 0)
    if sample_size < 3:  # Change threshold
        issues.append(f"Sample too small: {sample_size} rows")
    
    return issues
```

### Change test document query

Edit `test_tools_1_to_4.py`:

```python
query = """
    SELECT guid, title, doc_type, body_text
    FROM glean.drive_files
    WHERE doc_type = 'payroll'  # Filter for specific types
      AND LENGTH(body_text) > 1000  # Filter by size
    LIMIT 50
"""
```

### Adjust test population size

```bash
# Test on 100 documents instead of 50
python -c "
from test_tools_1_to_4 import main, fetch_test_documents
fetch_test_documents(limit=100)
main()
"
```

## Performance Tips

- **First run**: ~5-10 min for 50 docs (tool initialization + vLLM calls)
- **Cached runs**: ~2-3 min (schema code cache helps Tool 3)
- **Parallel runs**: Run on subsets in parallel for faster analysis

```bash
# Analyze first 25 documents
python test_tools_1_to_4.py | head -25

# Then analyze last 25 in parallel
python test_tools_1_to_4.py | tail -25
```

## Integration with CI/CD

Use the analyzer in your test pipeline:

```bash
#!/bin/bash
python test_tools_1_to_4.py

# Fail if more than 20% of documents failed
SUCCESS_RATE=$(jq '.summary | 
  (.tool1.success + .tool2.success + .tool3.success + .tool4.success) / 
  (((.tool1.success + .tool1.failed) * 4)) * 100' analysis/analysis_results.json)

if (( $(echo "$SUCCESS_RATE < 80" | bc -l) )); then
  echo "Pipeline success rate too low: $SUCCESS_RATE%"
  exit 1
fi
```

---

**Last Updated**: 2026-08-13
**Analyzer Version**: 1.0
