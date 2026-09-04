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
    parser.add_argument("--triage-category", type=str, default=None,
                        help="Override the source triage_category to select on (default: config.SOURCE_TRIAGE_CATEGORY)")
    parser.add_argument("--source-label", type=str, default=None,
                        help="Tag selected rows with this `source` value instead of the source table name -- "
                             "lets a second population share the source table under its own scoped label")
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

    kwargs = {"client": client, "source_limit": args.source_limit}
    if args.triage_category is not None:
        kwargs["triage_category"] = args.triage_category
    if args.source_label is not None:
        kwargs["source_label"] = args.source_label
    result = select_population(**kwargs)
    logger.info(f"Population selection complete: {result}")
    _print_counts(table_id, get_status_counts(client, table_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
