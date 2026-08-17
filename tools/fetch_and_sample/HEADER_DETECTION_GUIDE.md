# Header Row Detection Guide

Tool 1 now supports finding headers at any row position, not just the first row.

## Use Cases

**Problem**: CSV/data files sometimes have metadata, comments, or version info before the actual headers.

```
# Version 1.0
# Generated: 2026-08-12
# Source: Production database
id,name,email      <-- Headers actually here (row 3)
1,Alice,alice@...
2,Bob,bob@...
```

**Solution**: Tool 1 can now find and use headers at any position.

---

## Three Ways to Specify Headers

### Option 1: Default (Headers at row 0)

Headers are assumed to be on the first row. This is the default behavior.

```python
{
  "source_path": "data.csv",
  "sample_size": 10
}
```

**Output**:
```json
{
  "actual_header_row_index": 0,
  "first_line_is_header": true,
  ...
}
```

---

### Option 2: Explicit Header Position

If you know exactly which row contains headers, specify it directly:

```python
{
  "source_path": "data.csv",
  "header_row_index": 3,  # Headers on row 3
  "sample_size": 10
}
```

**Use when**: You know or can determine the header row position programmatically.

**Output**:
```json
{
  "actual_header_row_index": 3,
  "first_line_is_header": true,
  ...
}
```

---

### Option 3: Heuristic Search (Smart Detection)

Tool 1 searches the first 10 rows to find one that looks like headers:

```python
{
  "source_path": "data.csv",
  "find_header_heuristic": true,
  "sample_size": 10
}
```

**How it works**:
- Scores each of the first 10 rows
- Looks for rows with common header keywords: id, name, email, title, date, etc.
- Penalizes numeric-only rows (those are data, not headers)
- Returns the row with the highest "header score"

**Use when**: You don't know where headers are; Tool 1 will find them.

**Output**:
```json
{
  "actual_header_row_index": 3,  # Found on row 3
  "first_line_is_header": true,
  ...
}
```

---

## Examples

### Example 1: File with Metadata Comments

File structure:
```
# Comment: This is production data
# Date: 2026-08-12
# Version: 1.0
id,name,email
1,Alice,alice@test.com
2,Bob,bob@test.com
```

**With explicit index**:
```python
response = tool({
  "source_path": "data_with_comments.csv",
  "header_row_index": 3,
})
# → Returns: actual_header_row_index = 3
```

**With heuristic**:
```python
response = tool({
  "source_path": "data_with_comments.csv",
  "find_header_heuristic": true,
})
# → Finds row 3 automatically
```

---

### Example 2: File with Numeric Data Before Headers

File structure:
```
100,200,300
50,60,70
id,value,count
1,100,5
2,200,10
```

**With heuristic** (recommended here):
```python
response = tool({
  "source_path": "numeric_before_headers.csv",
  "find_header_heuristic": true,
})
# → Skips numeric rows, finds row 2 with id,value,count
# → actual_header_row_index = 2
```

---

### Example 3: Standard CSV (No Special Headers)

File structure:
```
id,name,email
1,Alice,alice@test.com
2,Bob,bob@test.com
```

**Just use the default**:
```python
response = tool({
  "source_path": "standard.csv",
})
# → actual_header_row_index = 0
```

---

## Header Scoring Heuristic

When using `find_header_heuristic: true`, Tool 1 scores rows based on:

| Factor | Points |
|--------|--------|
| Contains header keywords (id, name, email, etc.) | +2.0 |
| Looks like a label (alphabetic + underscore) | +0.5 |
| Contains numeric value | -0.5 |
| Contains non-label characters | -0.2 |
| Bonus if 70%+ of tokens look like labels | +1.0 |

**Example scores**:
- `id,name,email` → Score: ~7.0 (high - likely header)
- `1,2,3` → Score: -1.5 (low - numeric data)
- `user_id,full_name,contact_email` → Score: ~8.0 (very high - keywords!)

---

## Response Fields

Tool 1 now returns two header-related fields:

| Field | Type | Description |
|-------|------|-------------|
| `actual_header_row_index` | integer | Which row was used as the header (0-indexed) |
| `first_line_is_header` | boolean | Whether the row at `actual_header_row_index` looks like a header |

Example response:
```json
{
  "status": "success",
  "actual_header_row_index": 3,
  "first_line_is_header": true,
  "raw_sample": "...",
  "detected_format_hint": "csv",
  "source_type": "local_file",
  "total_bytes": 2847,
  "sample_size": 3,
  "error": null,
  "timestamp": "2026-08-12T21:10:08.314129+00:00"
}
```

---

## When to Use Each Option

| Scenario | Option | Why |
|----------|--------|-----|
| Headers on row 0 | Default (no params) | Simplest, fastest |
| Headers at known position | `header_row_index` | Deterministic, no guessing |
| Unknown header position | `find_header_heuristic` | Automatic discovery |
| Mixed files (variable structure) | `find_header_heuristic` | Adapts to each file |
| Large files with many preamble rows | `header_row_index` | Explicit is faster than heuristic |

---

## Integration with Tool 2

Tool 2 (infer_schema_and_profile) receives:
- `raw_sample`: The complete sample including all preamble rows
- `actual_header_row_index`: Which row is the header
- `first_line_is_header`: Whether headers were detected

Tool 2 can then:
1. Extract the correct header row from the sample
2. Analyze only the data rows (skipping preamble)
3. Infer column types correctly

---

## Tests

All 6 header detection tests pass:
- ✅ Headers at row 0 (default)
- ✅ Headers at explicit row 3
- ✅ Heuristic finds headers in row 3
- ✅ Detects headers with keyword patterns
- ✅ Skips numeric data rows, finds text headers
- ✅ Handles empty files gracefully

Run tests:
```bash
python -m tools.fetch_and_sample.test_header_detection
```
