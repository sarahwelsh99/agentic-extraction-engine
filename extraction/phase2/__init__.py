"""Phase 2: Safety Validation & Testing

This phase validates generated extraction code before execution:
1. AST-based code safety inspection
2. Deterministic test execution
3. Schema compliance validation

All work in this phase is deterministic (no LLMs).

Key modules:
- code_validator.py: AST inspection for dangerous patterns
- test_runner.py: Run extractors on test samples
- schema_validator.py: Verify output structure
"""
