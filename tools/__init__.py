"""Agent Tools Package

The extraction pipeline, in order:

  1  fetch_and_sample        fetch the source and take a representative sample
  2  delimiter_detector      report how the document is laid out
  3  generate_parser_script  write a deterministic parser from that report
  4  sandbox_execute         run the parser over the whole document
  5  evaluate_extraction     decide whether the extraction worked
  6  load_to_bigquery        load a passing extraction, one table per guid

Each tool is self-contained in its own directory with tool.py and test_tool.py.
"""

from tools.fetch_and_sample.tool import FetchAndSampleTool
from tools.delimiter_detector.tool import DelimiterDetectorTool
from tools.generate_parser_script.tool import GenerateParserScriptTool
from tools.sandbox_execute.tool import SandboxExecuteTool
from tools.evaluate_extraction.tool import EvaluateExtractionTool
from tools.load_to_bigquery.tool import LoadToBigQueryTool

__all__ = [
    "FetchAndSampleTool",
    "DelimiterDetectorTool",
    "GenerateParserScriptTool",
    "SandboxExecuteTool",
    "EvaluateExtractionTool",
    "LoadToBigQueryTool",
    "get_all_tools",
    "get_tool_by_name",
]

# Pipeline order, for callers that want to walk the stages
PIPELINE = [
    "fetch_and_sample",
    "delimiter_detector",
    "generate_parser_script",
    "sandbox_execute",
    "evaluate_extraction",
    "load_to_bigquery",
]

_TOOLS = {
    "fetch_and_sample": FetchAndSampleTool,
    "delimiter_detector": DelimiterDetectorTool,
    "generate_parser_script": GenerateParserScriptTool,
    "sandbox_execute": SandboxExecuteTool,
    "evaluate_extraction": EvaluateExtractionTool,
    "load_to_bigquery": LoadToBigQueryTool,
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
        name: Tool name, e.g. "delimiter_detector"

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
