"""Agentic extraction pipeline modules.

Core infrastructure:
- core: Shared services (config, BigQuery, vLLM, GPU monitoring, storage)

Phases:
- phase1: Pattern analysis & code generation (LLM-driven)
- phase2: Safety validation & testing (deterministic)
- phase3: Quality feedback loop (scaffolding)
- phase4: Deterministic execution at scale (scaffolding)

Schemas:
- schemas: PII extraction schema definitions

Tests:
- tests: Test suite
"""
