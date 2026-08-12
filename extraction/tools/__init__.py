"""Agent tools for agentic data ingestion pipeline.

Five independent, modular tools that the agent can call:
1. fetch_and_sample: Fetch raw data and return sample
2. infer_schema_and_profile: Analyze structure and data types
3. generate_parser_script: Generate Python parsing code
4. sandbox_run_and_evaluate: Execute and validate parser
5. load_to_bigquery: Load to BigQuery
"""
