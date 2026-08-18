"""Agent Tools Package

The extraction pipeline, in order - named here after the state machine's
roles (see extraction/core/pipeline_agent.py):

  1  fetch_and_sample        Looker (Micro-Slicer): fetch the source, slice a
                              bounded head+tail window
  2  structural_inspector    Looker (Structural Inspector): LLM-based
                              structural spec - header/footer bounds,
                              delimiter, null values
  3  generate_parser_script  Thinker: write a deterministic parser from that spec
  4  sandbox_execute         Tester: run the parser over the whole document
  5  evaluate_extraction     Eval: decide whether the extraction worked
  6  write_parquet_to_gcs    Deliver: write a passing extraction, one Parquet
                              file per guid

Each tool is self-contained in its own directory with tool.py and test_tool.py.
"""

from tools.fetch_and_sample.tool import FetchAndSampleTool
from tools.structural_inspector.tool import StructuralInspectorTool
from tools.generate_parser_script.tool import GenerateParserScriptTool
from tools.sandbox_execute.tool import SandboxExecuteTool
from tools.evaluate_extraction.tool import EvaluateExtractionTool
from tools.write_parquet_to_gcs.tool import WriteParquetToGcsTool

__all__ = [
    "FetchAndSampleTool",
    "StructuralInspectorTool",
    "GenerateParserScriptTool",
    "SandboxExecuteTool",
    "EvaluateExtractionTool",
    "WriteParquetToGcsTool",
    "get_all_tools",
    "get_tool_by_name",
]

# Pipeline order, for callers that want to walk the stages
PIPELINE = [
    "fetch_and_sample",
    "structural_inspector",
    "generate_parser_script",
    "sandbox_execute",
    "evaluate_extraction",
    "write_parquet_to_gcs",
]

_TOOLS = {
    "fetch_and_sample": FetchAndSampleTool,
    "structural_inspector": StructuralInspectorTool,
    "generate_parser_script": GenerateParserScriptTool,
    "sandbox_execute": SandboxExecuteTool,
    "evaluate_extraction": EvaluateExtractionTool,
    "write_parquet_to_gcs": WriteParquetToGcsTool,
}


def get_all_tools() -> list:
    """Instantiate every tool that can be built without external configuration.

    Tools needing credentials or a project are skipped rather than raising, so
    callers can enumerate the pipeline without a configured environment.
    """
    tools = []
    for name in PIPELINE:
        tool = get_tool_by_name(name)
        if tool is not None:
            tools.append(tool)
    return tools


def get_tool_by_name(name: str):
    """Get a tool by name using lazy instantiation.

    Args:
        name: Tool name, e.g. "structural_inspector"

    Returns:
        Tool instance, or None if unknown or not constructible here
    """
    tool_class = _TOOLS.get(name)
    if tool_class is None:
        return None

    try:
        return tool_class()
    except Exception as e:
        print(f"Error instantiating {name}: {e}")
        return None
