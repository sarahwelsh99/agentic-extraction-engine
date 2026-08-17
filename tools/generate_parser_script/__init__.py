"""Tool 3: Generate Parser Script

Takes the schema inferred by Tool 2 and generates production-ready
Python extraction code using vLLM (Qwen3-Coder-30B).

Uses LLM for high-quality code generation tailored to actual data patterns.
"""

from tools.generate_parser_script.tool import GenerateParserScriptTool

__all__ = ["GenerateParserScriptTool"]
