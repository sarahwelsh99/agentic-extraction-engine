"""Phase 2.1: Code Safety Validation

Inspects generated extraction code using AST analysis to prevent
dangerous patterns before execution.

Checks for:
- Command execution (exec, eval, system calls)
- File operations
- Network operations
- Import of unsafe modules
- Other suspicious patterns

All checks are deterministic (no LLM).
"""
import logging
import ast
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# Whitelist of safe modules that can be imported
SAFE_IMPORTS = {
    're', 'json', 'datetime', 'dataclasses', 'collections',
    'typing', 'math', 'string', 'operator', 'functools',
    'itertools', 'decimal', 'uuid', 'hashlib', 'base64'
}

# Dangerous functions/attributes that should not be called
DANGEROUS_CALLS = {
    'exec', 'eval', '__import__', 'compile', 'input', 'open',
    'file', 'getattr', 'setattr', 'delattr', 'hasattr',
    'vars', 'dir', 'globals', 'locals', 'breakpoint',
    'system', 'call', 'popen', 'spawn', 'fork',
}

# Dangerous attributes (os.*, subprocess.*, etc.)
DANGEROUS_MODULES = {
    'os', 'sys', 'subprocess', 'socket', 'urllib', 'requests',
    'pickle', 'shelve', 'marshal', 'ctypes', 'importlib',
    '__main__', 'builtins'
}


class ASTValidator(ast.NodeVisitor):
    """AST visitor to check for dangerous code patterns."""

    def __init__(self):
        self.violations: List[Tuple[int, str]] = []

    def visit_Import(self, node: ast.Import) -> None:
        """Check imports for dangerous modules."""
        for alias in node.names:
            module = alias.name.split('.')[0]
            if module not in SAFE_IMPORTS:
                self.violations.append((
                    node.lineno,
                    f"Unsafe import: {alias.name} (not in whitelist)"
                ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Check 'from X import Y' statements."""
        if node.module:
            module = node.module.split('.')[0]
            if module not in SAFE_IMPORTS:
                self.violations.append((
                    node.lineno,
                    f"Unsafe import: from {node.module} (not in whitelist)"
                ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Check function calls for dangerous functions."""
        if isinstance(node.func, ast.Name):
            if node.func.id in DANGEROUS_CALLS:
                self.violations.append((
                    node.lineno,
                    f"Dangerous function call: {node.func.id}"
                ))
        elif isinstance(node.func, ast.Attribute):
            # Check for os.system, subprocess.call, etc.
            if isinstance(node.func.value, ast.Name):
                module = node.func.value.id
                if module in DANGEROUS_MODULES:
                    self.violations.append((
                        node.lineno,
                        f"Dangerous module access: {module}.{node.func.attr}"
                    ))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check attribute access for dangerous patterns."""
        # Prevent access to __dict__, __code__, etc.
        if node.attr.startswith('__') and node.attr.endswith('__'):
            self.violations.append((
                node.lineno,
                f"Dunder access (potential security issue): {node.attr}"
            ))
        self.generic_visit(node)


def validate_code_safety(code: str) -> Dict[str, Any]:
    """Validate Python code for safety using AST analysis.

    Args:
        code: Python source code to validate

    Returns:
        Dict with status, violations, and summary
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {
            "status": "REJECTED",
            "reason": "SyntaxError",
            "error": str(e),
            "violations": []
        }

    validator = ASTValidator()
    validator.visit(tree)

    if validator.violations:
        return {
            "status": "REJECTED",
            "reason": "Security violations found",
            "violations": [
                {"line": line, "message": msg}
                for line, msg in validator.violations
            ],
            "violation_count": len(validator.violations)
        }

    return {
        "status": "APPROVED",
        "reason": "Code passed safety validation",
        "violations": [],
        "violation_count": 0
    }


def test_import_extractors(code: str) -> Dict[str, Any]:
    """Try to import and test basic functionality of extracted code.

    Args:
        code: Generated Python code

    Returns:
        Dict with import success, available functions, etc.
    """
    try:
        # Create a safe namespace
        namespace: Dict[str, Any] = {
            '__builtins__': {
                # Only allow safe builtins
                'len': len,
                'str': str,
                'int': int,
                'float': float,
                'list': list,
                'dict': dict,
                'set': set,
                'tuple': tuple,
                'bool': bool,
                'None': None,
                'True': True,
                'False': False,
            }
        }

        # Execute code in restricted namespace
        exec(code, namespace)

        # Check for required function
        if 'extract_pii' not in namespace:
            return {
                "status": "error",
                "error": "extract_pii function not defined",
                "available_functions": [k for k in namespace if callable(namespace[k])]
            }

        return {
            "status": "success",
            "available_functions": [k for k in namespace if callable(namespace[k]) and not k.startswith('_')],
            "extract_pii_signature": "extract_pii(title: str, body_text: str) -> dict"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
