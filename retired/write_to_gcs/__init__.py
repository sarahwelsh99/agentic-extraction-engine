"""Tool 5: Write to GCS

Writes extraction results as NDJSON to GCS with Hive-style partitioning.
Follows mosaic-glean-extraction's output_store.py pattern.

Input: Extracted rows from Tool 4
Output: NDJSON files in GCS with proper partitioning and audit metadata
"""

from tools.write_to_gcs.tool import WriteToGcsTool

__all__ = ["WriteToGcsTool"]
