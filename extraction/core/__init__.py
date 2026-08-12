"""Core infrastructure services for the extraction pipeline.

Shared services used by all phases:
- config: Configuration management
- bigquery_service: BigQuery operations
- llm_service: Local vLLM client
- gpu_monitor: GPU utilization tracking
- Storage: Work queue, status ledger, output store
"""
