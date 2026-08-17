"""CLI for population selection. Run with: python -m population_selection"""
import argparse
import logging
import sys

from google.api_core.exceptions import NotFound

from .selector import (
    get_bigquery_client,
    get_status_table_id,
    get_status_counts,
    select_population,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _print_counts(table_id: str, counts: dict) -> None:
    print(f"\nPopulation state in {table_id}:")
    for status, n in sorted(counts.items()):
        print(f"  {status:20s} {n}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__ or "Population selection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true",
                        help="Actually run the regex pass; without this, only report current counts")
    parser.add_argument("--source-limit", type=int, default=None,
                        help="Cap how many source rows the regex pass scores this run (for smoke testing)")
    args = parser.parse_args()

    client = get_bigquery_client()
    table_id = get_status_table_id()

    if not args.execute:
        try:
            _print_counts(table_id, get_status_counts(client, table_id))
        except NotFound:
            print(f"\n{table_id} doesn't exist yet -- it's created on first --execute run.\n")
        print("Dry run. Pass --execute to run the regex pass.\n")
        return 0

    result = select_population(
        client=client,
        source_limit=args.source_limit,
    )
    logger.info(f"Population selection complete: {result}")
    _print_counts(table_id, get_status_counts(client, table_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
