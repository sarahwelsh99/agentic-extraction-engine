#!/usr/bin/env python3
"""Create the extraction dataset and table.

Run once before the pipeline loads anything. Provisioning is deliberately its own
step: if the loader created the table on first use, the schema would appear on
whichever machine happened to run first, and a later change would drift silently.

The schema comes from LoadToBigQueryTool.SCHEMA, so there is one definition of it
rather than two that can disagree.

Usage:
    python scripts/provision_extraction_table.py                 # show the plan
    python scripts/provision_extraction_table.py --create        # create it
    python scripts/provision_extraction_table.py --create \\
        --dataset extraction_smoke_test                          # somewhere else
"""

import argparse
import logging
import sys

from google.cloud import bigquery

from extraction.core import config
from tools.load_to_bigquery.tool import (
    DEFAULT_DATASET,
    DEFAULT_TABLE,
    LoadToBigQueryTool,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def describe(project: str, dataset: str, table: str) -> None:
    """Print exactly what would be created."""
    print(f"\nTarget:      {project}.{dataset}.{table}")
    print("Partitioned: DAY on extracted_at")
    print("Clustered:   guid")
    print("\nSchema:")
    for field in LoadToBigQueryTool.SCHEMA:
        print(f"  {field.name:14s} {field.field_type:10s} {field.mode:9s} {field.description}")
    print()


def provision(project: str, dataset: str, table: str) -> int:
    """Create the dataset and table if they are not already there.

    Returns:
        Process exit code
    """
    client = bigquery.Client(project=project)
    dataset_id = f"{project}.{dataset}"
    table_id = f"{dataset_id}.{table}"

    try:
        client.get_dataset(dataset_id)
        logger.info(f"Dataset already exists: {dataset_id}")
    except Exception:
        client.create_dataset(dataset_id, exists_ok=True)
        logger.info(f"Created dataset: {dataset_id}")

    try:
        existing = client.get_table(table_id)
        logger.info(
            f"Table already exists: {table_id} "
            f"({len(existing.schema)} columns, {existing.num_rows} rows)"
        )
        # Report drift rather than altering a table that already holds data
        have = {f.name for f in existing.schema}
        want = {f.name for f in LoadToBigQueryTool.SCHEMA}
        if have != want:
            logger.warning(
                f"Schema differs from the tool's definition. "
                f"Missing: {sorted(want - have) or 'none'}. "
                f"Unexpected: {sorted(have - want) or 'none'}."
            )
            return 1
        return 0
    except Exception:
        pass

    bq_table = bigquery.Table(table_id, schema=LoadToBigQueryTool.SCHEMA)
    bq_table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="extracted_at",
    )
    bq_table.clustering_fields = ["guid"]
    bq_table.description = (
        "Rows extracted from glean structured records. Each document's own "
        "columns are carried in the JSON 'data' column. Loads append, so read "
        "the latest generation per row with QUALIFY ROW_NUMBER() OVER "
        "(PARTITION BY guid, row_number ORDER BY extracted_at DESC) = 1."
    )
    client.create_table(bq_table)
    logger.info(f"Created table: {table_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true",
                        help="Actually create them; without this, only print the plan")
    parser.add_argument("--project", default=config.PROJECT_ID,
                        help="GCP project (default: PROJECT_ID from config)")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    args = parser.parse_args()

    if not args.project:
        logger.error("No project. Set PROJECT_ID or pass --project.")
        return 2

    describe(args.project, args.dataset, args.table)

    if not args.create:
        print("Dry run. Pass --create to create the dataset and table.\n")
        return 0

    return provision(args.project, args.dataset, args.table)


if __name__ == "__main__":
    sys.exit(main())
