# Agent Tools Usage Guide

This directory contains all independent, modular tools that the agent can call during execution.

## Directory Structure

```
tools/
├── base.py                             # Base class for all tools
├── __init__.py                         # Tool registry & loader
├── fetch_and_sample/                   # Tool 1: Fetch and sample data
│   ├── __init__.py
│   ├── tool.py                         # Main implementation
│   ├── test_tool.py                    # Unit tests
│   └── demo.py                         # Demo/validation
├── infer_schema_and_profile/           # Tool 2: Infer schema (scaffolding)
├── generate_parser_script/             # Tool 3: Generate code (scaffolding)
├── sandbox_run_and_evaluate/           # Tool 4: Test code (scaffolding)
└── load_to_bigquery/                   # Tool 5: Load to BQ (scaffolding)
```

## Tool Interface

All tools inherit from `AgentTool` base class and provide:

```python
class AgentTool:
    @property
    def name(self) -> str:
        """Unique tool identifier"""

    @property
    def description(self) -> str:
        """One-line description"""

    @property
    def input_schema(self) -> Dict:
        """JSONSchema for inputs"""

    @property
    def output_schema(self) -> Dict:
        """JSONSchema for outputs"""

    def execute(self, input_data: Dict) -> ToolResponse:
        """Execute tool logic"""

    def __call__(self, input_data: Dict) -> str:
        """Call tool and return JSON response"""
```

## How the Agent Uses Tools

### 1. Discover Available Tools

```python
from tools import get_all_tools, get_tool_by_name

# Get all tools
all_tools = get_all_tools()
for tool in all_tools:
    print(f"{tool.name}: {tool.description}")

# Output:
# fetch_and_sample: Fetch raw data from a source...
# infer_schema_and_profile: Analyze data structure... (coming soon)
# generate_parser_script: Generate Python code... (coming soon)
# ...
```

### 2. Get Tool Metadata

```python
tool = get_tool_by_name("fetch_and_sample")

# Inspect what the tool expects
print(tool.input_schema)
# {
#   "properties": {
#     "source_path": {"type": "string", ...},
#     "sample_size": {"type": "integer", ...},
#     ...
#   },
#   "required": ["source_path"]
# }

# Inspect what the tool returns
print(tool.output_schema)
```

### 3. Call a Tool

```python
import json

tool = get_tool_by_name("fetch_and_sample")

# Prepare input
input_data = {
    "source_path": "my-project.my_dataset.my_table",
    "sample_size": 10,
}

# Call tool (returns JSON string)
response_json = tool(input_data)

# Parse response
response = json.loads(response_json)

if response["status"] == "success":
    raw_sample = response["raw_sample"]
    format_hint = response["detected_format_hint"]
    # Pass to next tool...
else:
    error = response["error"]
    # Handle error...
```

## Tool Response Format

All tools return standardized JSON:

```json
{
  "status": "success|error|partial_success|warning",
  "error": null,
  "timestamp": "2026-08-12T21:10:08.314129+00:00",
  "...": "tool-specific fields"
}
```

## Example: Complete Agent Loop

```python
import json
from tools import get_tool_by_name

def agent_loop(source_path: str):
    """Example agent that uses tools to process data."""

    # Step 1: Fetch sample data
    fetch_tool = get_tool_by_name("fetch_and_sample")
    step1_result = json.loads(fetch_tool({
        "source_path": source_path,
        "sample_size": 20,
    }))

    if step1_result["status"] != "success":
        return f"Error in step 1: {step1_result['error']}"

    print(f"✓ Fetched sample: {step1_result['sample_size']} rows")
    raw_sample = step1_result["raw_sample"]
    format_hint = step1_result["detected_format_hint"]

    # Step 2: Infer schema and profile
    infer_tool = get_tool_by_name("infer_schema_and_profile")
    step2_result = json.loads(infer_tool({
        "raw_sample": raw_sample,
        "file_format_hint": format_hint,
    }))

    if step2_result["status"] != "success":
        return f"Error in step 2: {step2_result['error']}"

    print(f"✓ Inferred schema: {len(step2_result['columns'])} columns")
    schema_profile = step2_result["columns"]

    # Step 3: Generate parser code
    gen_tool = get_tool_by_name("generate_parser_script")
    step3_result = json.loads(gen_tool({
        "schema_profile": schema_profile,
    }))

    if step3_result["status"] != "success":
        return f"Error in step 3: {step3_result['error']}"

    print(f"✓ Generated parser code")
    generated_code = step3_result["generated_code"]

    # Step 4: Test code in sandbox
    sandbox_tool = get_tool_by_name("sandbox_run_and_evaluate")
    step4_result = json.loads(sandbox_tool({
        "generated_code": generated_code,
        "raw_sample": raw_sample,
    }))

    if step4_result["status"] != "success":
        return f"Error in step 4: {step4_result['error']}"

    if not step4_result["validation"]["all_checks_passed"]:
        return f"Validation failed: {step4_result['validation']['warnings']}"

    print(f"✓ Code passed validation")

    # Step 5: Load to BigQuery
    load_tool = get_tool_by_name("load_to_bigquery")
    step5_result = json.loads(load_tool({
        "data_path": "gs://bucket/parsed_data.csv",
        "project_id": "my-project",
        "dataset_id": "my_dataset",
        "table_id": "my_table",
    }))

    if step5_result["status"] != "success":
        return f"Error in step 5: {step5_result['error']}"

    rows_loaded = step5_result["total_rows_loaded"]
    print(f"✓ Loaded {rows_loaded} rows to BigQuery")

    return "Pipeline completed successfully!"

# Run the agent
result = agent_loop("my-project.raw_data.events")
print(result)
```

## Adding a New Tool

To add Tool 6, follow this pattern:

1. Create directory: `tools/my_tool/`
2. Create `tool.py` inheriting from `AgentTool`
3. Create `test_tool.py` with unit tests
4. Create `demo.py` with example usage
5. Create `__init__.py` exporting the tool
6. Update `tools/__init__.py` to register the tool

```python
# tools/my_tool/tool.py
from tools.base import AgentTool, ToolResponse

class MyToolTool(AgentTool):
    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "Does something useful"

    @property
    def input_schema(self) -> Dict:
        return {"properties": {...}, "required": [...]}

    @property
    def output_schema(self) -> Dict:
        return {"properties": {...}, "required": [...]}

    def execute(self, input_data: Dict) -> ToolResponse:
        # Implementation here
        return ToolResponse(status="success", ...)

# tools/__init__.py (updated)
from tools.my_tool.tool import MyToolTool

def get_all_tools() -> list:
    return [
        FetchAndSampleTool(),
        MyToolTool(),  # <-- Add here
        ...
    ]
```

## Testing Tools

Each tool has its own test file:

```bash
# Test Tool 1
python -m tools.fetch_and_sample.test_tool

# Test all tools (when more are implemented)
python -m pytest tools/*/test_tool.py -v
```

## Tool Status

| Tool | Status | Implementation |
|------|--------|-----------------|
| 1. fetch_and_sample | ✅ Complete | 5 tests passing |
| 2. infer_schema_and_profile | ⏳ Scaffolding | Interface defined |
| 3. generate_parser_script | ⏳ Scaffolding | Interface defined |
| 4. sandbox_run_and_evaluate | ⏳ Scaffolding | Interface defined |
| 5. load_to_bigquery | ⏳ Scaffolding | Interface defined |

## Next Steps

1. ✅ Tool 1 (fetch_and_sample) - COMPLETE
2. ⏳ Tool 2 (infer_schema_and_profile) - TO BUILD
3. ⏳ Tool 3 (generate_parser_script) - TO BUILD
4. ⏳ Tool 4 (sandbox_run_and_evaluate) - TO BUILD
5. ⏳ Tool 5 (load_to_bigquery) - TO BUILD
6. ⏳ Agent loop - TO BUILD

Once all tools are complete, they integrate into the agent loop for end-to-end data processing.
