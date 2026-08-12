"""Agent Tools Package

Collection of independent, modular tools that the agent can call.

Each tool is self-contained in its own directory with:
- tool.py: The actual tool implementation
- test_tool.py: Unit tests
- demo.py: Example usage and validation demo
"""

from tools.fetch_and_sample.tool import FetchAndSampleTool

__all__ = [
    "FetchAndSampleTool",
    "get_all_tools",
    "get_tool_by_name",
]


def get_all_tools() -> list:
    """Get all available tools.

    Returns:
        List of tool instances
    """
    return [
        FetchAndSampleTool(),
        # More tools will be added here as we build them
    ]


def get_tool_by_name(name: str):
    """Get a tool by name.

    Args:
        name: Tool name (e.g., "fetch_and_sample")

    Returns:
        Tool instance or None if not found
    """
    tools = {tool.name: tool for tool in get_all_tools()}
    return tools.get(name)
