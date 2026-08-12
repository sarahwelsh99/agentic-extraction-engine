# Tools Architecture

A dedicated, well-organized tools directory that the agent will call to execute its workflow.

## Directory Structure

```
tools/
├── base.py                              Base class for all tools
├── __init__.py                          Tool registry & loader API
├── AGENT_USAGE_EXAMPLE.md               Complete usage guide for agent
├── demo_agent_calling_tools.py          Demo showing agent tool discovery/calling
│
├── fetch_and_sample/                    ✅ TOOL 1: Complete
│   ├── __init__.py
│   ├── tool.py                          Main implementation (class FetchAndSampleTool)
│   ├── test_tool.py                     5 unit tests (all passing)
│   └── demo.py                          Validation demo
│
├── infer_schema_and_profile/            ⏳ TOOL 2: Scaffolding
│   └── __init__.py
│
├── generate_parser_script/              ⏳ TOOL 3: Scaffolding
│   └── __init__.py
│
├── sandbox_run_and_evaluate/            ⏳ TOOL 4: Scaffolding
│   └── __init__.py
│
└── load_to_bigquery/                    ⏳ TOOL 5: Scaffolding
    └── __init__.py
```

## Tool Base Class

All tools inherit from `AgentTool` (tools/base.py):

```python
class AgentTool(ABC):
    @property
    def name(self) -> str:
        """Unique tool identifier"""

    @property
    def description(self) -> str:
        """One-line description of what the tool does"""

    @property
    def input_schema(self) -> Dict:
        """JSONSchema for input validation"""

    @property
    def output_schema(self) -> Dict:
        """JSONSchema for output validation"""

    def execute(self, input_data: Dict) -> ToolResponse:
        """Execute the tool logic"""

    def __call__(self, input_data: Dict) -> str:
        """Call tool and return JSON response"""
```

## Tool Registry API

The agent discovers and uses tools through a simple API:

```python
from tools import get_all_tools, get_tool_by_name

# Discover all tools
tools = get_all_tools()  # Returns list of AgentTool instances

# Get a tool by name
tool = get_tool_by_name("fetch_and_sample")

# Inspect tool interface
input_schema = tool.input_schema
output_schema = tool.output_schema
description = tool.description

# Call tool (returns JSON string)
response_json = tool({"source_path": "path/to/data"})
response = json.loads(response_json)
```

## Response Format

All tools return standardized JSON:

```json
{
  "status": "success|error|partial_success|warning",
  "error": null,
  "timestamp": "2026-08-12T21:10:08.314129+00:00",
  "...": "tool-specific fields"
}
```

## Adding a New Tool

To implement Tool 2 (or any new tool):

### 1. Create directory structure
```
tools/infer_schema_and_profile/
├── __init__.py
├── tool.py
├── test_tool.py
└── demo.py
```

### 2. Implement the tool (tool.py)
```python
from tools.base import AgentTool, ToolResponse

class InferSchemaAndProfileTool(AgentTool):
    @property
    def name(self) -> str:
        return "infer_schema_and_profile"

    @property
    def description(self) -> str:
        return "Analyze raw data to infer schema and profile columns"

    @property
    def input_schema(self) -> Dict:
        return {
            "properties": {
                "raw_sample": {"type": "string"},
                "file_format_hint": {"type": "string"},
                # ... more fields
            },
            "required": ["raw_sample"],
        }

    @property
    def output_schema(self) -> Dict:
        return {
            "properties": {
                "detected_format": {"type": "string"},
                "columns": {"type": "array"},
                # ... more fields
            },
            "required": ["status", "error", "timestamp"],
        }

    def execute(self, input_data: Dict) -> ToolResponse:
        # Implementation here
        return ToolResponse(
            status="success",
            detected_format="csv",
            columns=[...],
            error=None,
        )
```

### 3. Write tests (test_tool.py)
```python
from tools.infer_schema_and_profile.tool import InferSchemaAndProfileTool

def test_analyze_csv():
    tool = InferSchemaAndProfileTool()
    result = json.loads(tool({
        "raw_sample": "id,name,email\n1,John,john@test.com\n",
    }))
    assert result["status"] == "success"
    assert result["detected_format"] == "csv"
```

### 4. Register the tool (tools/__init__.py)
```python
from tools.infer_schema_and_profile.tool import InferSchemaAndProfileTool

def get_all_tools() -> list:
    return [
        FetchAndSampleTool(),
        InferSchemaAndProfileTool(),  # ← Add here
        # ... more tools
    ]
```

## Tool Status

| # | Tool | Status | Tests | Location |
|---|------|--------|-------|----------|
| 1 | fetch_and_sample | ✅ Complete | 5 passing | `tools/fetch_and_sample/` |
| 2 | infer_schema_and_profile | ⏳ Next | - | `tools/infer_schema_and_profile/` |
| 3 | generate_parser_script | ⏳ Pending | - | `tools/generate_parser_script/` |
| 4 | sandbox_run_and_evaluate | ⏳ Pending | - | `tools/sandbox_run_and_evaluate/` |
| 5 | load_to_bigquery | ⏳ Pending | - | `tools/load_to_bigquery/` |

## How the Agent Uses Tools

### 1. Initialization
```python
from tools import get_all_tools

# Agent discovers available tools on startup
available_tools = get_all_tools()
```

### 2. Tool Selection
```python
from tools import get_tool_by_name

# Agent selects the right tool for the current step
tool = get_tool_by_name("fetch_and_sample")
```

### 3. Tool Execution
```python
# Agent prepares input matching the tool's input_schema
input_data = {
    "source_path": "my-project.my_dataset.my_table",
    "sample_size": 20,
}

# Agent calls the tool
response_json = tool(input_data)
response = json.loads(response_json)

# Agent checks status and handles errors
if response["status"] == "success":
    # Use response data for next step
    raw_sample = response["raw_sample"]
else:
    # Handle error gracefully
    error = response["error"]
```

### 4. Tool Chaining
```python
# Agent chains tools together
step1_result = fetch_and_sample_tool(input1)
step2_result = infer_schema_tool(step1_result["raw_sample"])
step3_result = generate_parser_tool(step2_result["schema"])
step4_result = sandbox_tool(step3_result["generated_code"])
step5_result = load_tool(step4_result["validated_code"])
```

## Benefits of This Architecture

✅ **Modularity**: Each tool is independent and self-contained
✅ **Discoverability**: Agent can discover all available tools at runtime
✅ **Type Safety**: JSONSchema validation for inputs/outputs
✅ **Consistency**: All tools follow same interface and response format
✅ **Testability**: Each tool has its own test suite
✅ **Extensibility**: Easy to add new tools following the pattern
✅ **Error Handling**: Standardized error responses across all tools
✅ **Documentation**: Each tool is self-documenting via schema

## Running Tests

```bash
# Test individual tools
python -m tools.fetch_and_sample.test_tool

# Run all tool tests
python -m pytest tools/*/test_tool.py -v

# View agent using tools
python -m tools.demo_agent_calling_tools
```

## Next Steps

1. ✅ **Tool 1 Complete**: fetch_and_sample is fully implemented and tested
2. ⏳ **Build Tool 2**: infer_schema_and_profile
3. ⏳ **Build Tool 3**: generate_parser_script
4. ⏳ **Build Tool 4**: sandbox_run_and_evaluate
5. ⏳ **Build Tool 5**: load_to_bigquery
6. ⏳ **Build Agent Loop**: Orchestrate tools in sequence

Once all tools are complete, they integrate into the main agent loop via the registry.
