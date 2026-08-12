"""Phase 4: Deterministic Execution at Scale

This phase executes validated extraction code deterministically at scale
across millions of payloads:

1. Load: Fetch approved extractors from GCS
2. Queue: Build local SQLite work queue from metadata
3. Prefetch: Background thread fetches next batch's body_text
4. Execute: Worker threads run extractors (no LLM calls)
5. Write: Async writer flushes results to GCS + status ledger

Zero BigQuery writes during execution (status ledger pattern).

Key modules:
- executor.py: Main execution loop
- extractor_loader.py: Safe code loading with signature verification
- queue_prefetcher.py: Background batch fetching
- batch_writer.py: Async result writing
"""
