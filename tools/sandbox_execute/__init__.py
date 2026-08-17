"""Tool 4: Sandbox Run and Evaluate

Executes generated Python extraction code in isolated Docker container.
Computes quality metrics and uses hybrid validation (deterministic + LLM).

Input: Generated code from Tool 3 + full body_text from Tool 1
Output: Extracted rows + quality metrics + validation status
"""

from tools.sandbox_execute.tool import SandboxExecuteTool

__all__ = ["SandboxExecuteTool"]
