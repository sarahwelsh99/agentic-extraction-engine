"""Population selection: decide which documents feed the extraction pipeline.

One-time, rerunnable pass over glean.drive_files (filtered to
triage_category=INCL_STRUCTURED_RECORD) that flags exactly the documents
containing PII for extraction and excludes the rest. Independent of the rest
of the pipeline -- run it directly:

    python -m population_selection                              # dry run
    python -m population_selection --execute
    python -m population_selection --execute --source-limit 500    # smoke test

See selector.py for how documents are classified (ported from
mosaic-glean-extraction's prefilter.py).
"""
from .selector import select_population, run_regex_pass, get_status_counts

__all__ = [
    "select_population",
    "run_regex_pass",
    "get_status_counts",
]
