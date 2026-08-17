# Pipeline Analyzer Summary

Complete analyzer for Tools 1-4 with GUID tracking and inaccuracy detection.

## What It Does

```
50 Real Documents (from glean)
         ↓
  Tool 1: Fetch & Sample
         ↓
  Tool 2: Infer Schema
         ↓
  Tool 3: Generate Code
         ↓
  Tool 4: Sandbox Run
         ↓
  ANALYZER: Validate outputs + Identify issues
         ↓
  Reports with GUIDs:
  - JSON (complete results)
  - CSV (quick reference)
  - HTML (visual report)
  - JSONL (full outputs)
```

## Quick Start

```bash
# Run the analysis (5-10 minutes)
python test_tools_1_to_4.py

# View results
open analysis/analysis_report.html      # HTML report
cat analysis/document_details.csv       # CSV summary
jq . analysis/analysis_results.json     # Full JSON
```

## Files Created

### Core Analyzer
- `extraction/pipeline_analyzer.py` - Main analyzer + validators
- `test_tools_1_to_4.py` - Test runner script
- `ANALYZER_GUIDE.md` - Complete usage guide

### Output Directory (analysis/)
- `analysis_results.json` - Complete results with GUIDs
- `document_details.csv` - Quick reference with GUIDs
- `analysis_report.html` - Interactive HTML report
- `full_outputs.jsonl` - Raw tool outputs (JSONL)

## Key Features

### GUID Tracking
Every document is tracked by GUID throughout the pipeline:
```json
{
  "guid": "3aff74b7-1f1d-f5ae-e177-779175d64819",
  "title": "Employee Payroll",
  "overall_status": "failed",
  "tools": {
    "tool1": {"status": "success", "issues": []},
    "tool2": {"status": "success", "issues": []},
    "tool3": {"status": "failed", "issues": ["Tool 3: Missing parse_row method"]},
    "tool4": {"status": "skip", "issues": []}
  }
}
```

### Validation Rules
Each tool is validated for correctness:

**Tool 1**: Format detection, sample quality, header identification
**Tool 2**: Column inference, type validation, PII detection
**Tool 3**: Code generation, syntax validation, required structure
**Tool 4**: Extraction success, validation result, quality metrics

### Issue Categorization
Issues are grouped by type for easy pattern identification:
```json
{
  "issues_by_type": {
    "Tool 1": [...],
    "Tool 2": [...],
    "Tool 3": [...],
    "Tool 4": [...]
  }
}
```

## Example Output

### Summary Stats (from analysis_results.json)
```
Total Documents: 50
Tool Success Rates:
  Tool 1: 98% (49/50)
  Tool 2: 98% (49/50)  
  Tool 3: 92% (46/50)  ← Code generation bottleneck
  Tool 4: 88% (44/50)  ← Some extraction failures

Most Common Issues:
  - Tool 3: Missing parse_row method (3 occurrences)
  - Tool 4: Low success rate <70% (5 occurrences)
  - Tool 2: Invalid column type (2 occurrences)
```

### Per-Document View (from document_details.csv)
```csv
GUID,Title,Doc Type,Body Length,Overall Status,Tool1 Status,Tool1 Issues,Tool2 Status,Tool2 Issues,Tool3 Status,Tool3 Issues,Tool4 Status,Tool4 Issues
abc123...,Payroll Record,csv,5000,success,success,,success,,success,,success,
def456...,Invoice,json,3200,failed,success,,success,,failed,"Tool 3: Syntax errors",skip,
```

## Analysis Workflow

**1. Run analyzer**
```bash
python test_tools_1_to_4.py
```

**2. Check HTML report** (visual overview)
```bash
open analysis/analysis_report.html
```

**3. Find failed documents** (CSV for filtering)
```bash
# Open in Excel and filter by "Overall Status"
cat analysis/document_details.csv | grep failed
```

**4. Debug specific GUID** (JSONL for details)
```bash
# Extract specific document's full output
grep "3aff74b7-1f1d" analysis/full_outputs.jsonl | jq .
```

**5. Analyze patterns** (JSON for aggregations)
```python
import json
with open("analysis/analysis_results.json") as f:
    results = json.load(f)
    
# Which tool has most failures?
for tool, summary in results["summary"].items():
    fail_rate = summary["failed"] / (summary["success"] + summary["failed"]) * 100
    print(f"{tool}: {fail_rate:.1f}% failure rate")
```

## Inaccuracy Detection Examples

### Example 1: Tool 2 Type Inference Bug
**GUID**: `abc123def456`
**Issue**: "Tool 2: Column 'Salary' has invalid type: string_with_numbers"
**Found**: CSV with "50,000" (comma-formatted numbers) detected as string instead of float
**Fix**: Improve type inference to handle thousands separators

### Example 2: Tool 3 vLLM Format Issue
**GUID**: `def789ghi012`
**Issue**: "Tool 3: Missing parse_row method"
**Found**: vLLM generated code without `def parse_row()` method
**Fix**: Strengthen prompt or post-processing

### Example 3: Tool 4 Extraction Failure
**GUID**: `ghi345jkl678`
**Issue**: "Tool 4: Low success rate (45%)"
**Found**: Generated code works for 45% of rows, fails on others
**Fix**: Either improve code generation or data is too diverse

## Customization

### Change test population
Edit query in `test_tools_1_to_4.py`:
```python
# Test only payroll documents
WHERE doc_type = 'payroll'
  AND pii_category_score >= 7
```

### Modify validation rules
Edit `extraction/pipeline_analyzer.py`:
```python
def validate_tool3(self, guid: str, output):
    # Add custom checks
    if some_condition:
        issues.append("Custom validation failed")
```

### Run on different sample size
```bash
# Modify fetch_test_documents() call limit parameter
python -c "
import test_tools_1_to_4
documents = test_tools_1_to_4.fetch_test_documents(limit=100)
"
```

## Integration Points

The analyzer plugs into your existing pipeline:

```
Existing Pipeline:
  run_pipeline.py → Tools 1-5 → metrics.csv/json

New Analysis:
  test_tools_1_to_4.py → Tools 1-4 → analysis/
                          ↓
                      Validates outputs
                      Identifies issues
                      Tracks GUIDs
```

## Next Steps

After analysis:

1. **Review failed documents** - Understand patterns
2. **Prioritize fixes** - Fix tools with most failures first
3. **Update validators** - Add checks for issues you find
4. **Re-test** - Run analyzer again to verify fixes

---

**Status**: ✅ Ready to use
**Test Population**: 50 high-PII documents from glean
**Expected Duration**: 5-10 minutes
**Output Format**: JSON, CSV, HTML, JSONL (all with GUID tracking)
