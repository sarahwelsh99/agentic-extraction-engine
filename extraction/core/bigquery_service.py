"""BigQuery service for agentic extraction pipeline.

Adapted from mosaic-glean-extraction's bigquery_service.py.
Handles both status table operations and source data queries.
"""
import logging
from typing import Generator, List, Optional, Tuple
from google.cloud import bigquery
from google.api_core import exceptions as gexc
from . import config

logger = logging.getLogger(__name__)


def get_bigquery_client() -> bigquery.Client:
    """Get authenticated BigQuery client."""
    return bigquery.Client(project=config.PROJECT_ID)


def get_status_table_id(client: bigquery.Client) -> str:
    """Get fully qualified status table ID."""
    return f"{config.PROJECT_ID}.{config.DATASET_ID}.{config.SOURCE_TABLE_NAME}"


def initialize_status_table(client: bigquery.Client, table_id: str) -> None:
    """Create status table if it doesn't exist.

    Status table tracks extraction progress with columns:
    - guid: unique identifier
    - status: pending | complete | error | error_llm | error_truncated | error_oversized | dense
    - extraction_version: version of code that extracted this
    - extracted_at: timestamp when extraction completed
    - error_message: error details if status is error*
    - body_length: size of input document
    - body_text: input document text
    """
    schema = [
        bigquery.SchemaField("guid", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("extraction_version", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("extracted_at", "TIMESTAMP", mode="NULLABLE"),
        bigquery.SchemaField("error_message", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("body_length", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("body_text", "STRING", mode="NULLABLE"),
        # Bare source table name ("drive_files"). Lets more than one population
        # share this table, filtered by source on every read — the same reason
        # mosaic carries it. A single-population table can leave it uniform.
        bigquery.SchemaField("source", "STRING", mode="NULLABLE"),
    ]

    table = bigquery.Table(table_id, schema=schema)
    table.clustering_fields = ["status", "source", "guid"]

    try:
        table = client.create_table(table, exists_ok=True)
        logger.info(f"Status table {table_id} ready")
    except Exception as e:
        logger.error(f"Failed to create status table: {e}")
        raise

    # The table predates the source column where an earlier version created it.
    # Additive, so it is safe to run on every startup.
    client.query(f"""
        ALTER TABLE `{table_id}`
        ADD COLUMN IF NOT EXISTS source STRING
    """).result()


def _source_clause(source: Optional[str]) -> str:
    """Scope a status-table read to one population.

    mosaic keeps every datasource's population in one status table and filters
    every runtime read by `source`. Ours holds a single population today, so
    the column may be absent or uniformly filled; passing source=None reads the
    whole table rather than failing.
    """
    return f"      AND source = '{source}'\n" if source else ""


def fetch_pending_totals(
    client: bigquery.Client,
    status_table_id: str,
    source: Optional[str] = None,
    min_body_length: int = 50,
) -> Tuple[int, int]:
    """Count and total bytes of the pending backlog, for bin sizing.

    Read before the metadata stream so the number of bins is known up front:
    LPT packing needs its bin count before the first guid arrives.

    Returns:
        (guid count, total body bytes)
    """
    query = f"""
    SELECT COUNT(*) AS n, COALESCE(SUM(body_length), 0) AS total_bytes
    FROM `{status_table_id}`
    WHERE status = 'pending'
      AND body_text IS NOT NULL
      AND body_length >= {int(min_body_length)}
{_source_clause(source)}    """
    row = list(client.query(query).result())[0]
    return int(row.n), int(row.total_bytes)


def fetch_pending_metadata(
    client: bigquery.Client,
    status_table_id: str,
    source: Optional[str] = None,
    min_body_length: int = 50,
) -> Generator[Tuple[str, int], None, None]:
    """Stream (guid, body_length) for every pending guid, largest first.

    No body_text, so this is cheap even across a multi-million-row backlog, and
    the caller (workqueue.WorkQueue.build) only ever holds O(num_bins) state.

    Largest-first is what makes LPT packing work: the biggest documents are
    placed while every bin is still nearly empty, so the last, smallest ones
    are what levels the bins out.
    """
    query = f"""
    SELECT guid, body_length
    FROM `{status_table_id}`
    WHERE status = 'pending'
      AND body_text IS NOT NULL
      AND body_length >= {int(min_body_length)}
{_source_clause(source)}    ORDER BY body_length DESC
    """
    query_job = client.query(query)
    for row in query_job.result(page_size=50000):
        yield row.guid, row.body_length


def fetch_bodies_for_guids(
    client: bigquery.Client,
    status_table_id: str,
    guids: List[str],
    chunk_size: int = 15000
) -> List[dict]:
    """Just-in-time body_text fetch for one work-queue bin's guids.

    A targeted IN UNNEST against a handful of thousand guids is far cheaper
    than scanning the whole pending set. Chunked defensively so one oversized
    bin can't force a single huge query response.
    """
    rows = []
    guids = list(guids)
    for i in range(0, len(guids), chunk_size):
        chunk = guids[i:i + chunk_size]
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("guids", "STRING", chunk)])
        query = f"""
        SELECT guid, body_text, body_length
        FROM `{status_table_id}`
        WHERE guid IN UNNEST(@guids)
        """
        rows.extend(client.query(query, job_config=job_config).result())
    return [{"guid": r.guid, "body_text": r.body_text, "body_length": r.body_length}
            for r in rows]


def populate_status_table_from_source(
    client: bigquery.Client,
    status_table_id: str,
    source_project: str = config.SOURCE_PROJECT,
    source_table: str = config.SOURCE_TABLE,
    triage_category: str = config.SOURCE_TRIAGE_CATEGORY
) -> int:
    """Populate status table from source data (drive_files).

    Fetches guids and documents from source table matching triage_category.
    Returns count of rows inserted.
    """
    source_table_id = f"{source_project}.{source_table}"

    query = f"""
    INSERT INTO `{status_table_id}` (guid, status, body_length, body_text)
    SELECT
        id as guid,
        'pending' as status,
        LENGTH(body_text) as body_length,
        body_text
    FROM `{source_table_id}`
    WHERE triage_category = '{triage_category}'
      AND body_text IS NOT NULL
      AND LENGTH(body_text) > 0
    """

    try:
        job = client.query(query)
        result = job.result()
        count = job.total_rows
        logger.info(f"Populated status table with {count} rows from {source_table_id}")
        return count
    except Exception as e:
        logger.error(f"Failed to populate status table: {e}")
        raise


# Guids per status UPDATE. One statement per document does not survive contact
# with a corpus: concurrent DML against a single table serializes on a table
# lock and starts failing outright ("could not serialize access") once a handful
# are queued, so marks are batched and issued from one writer.
MARK_CHUNK = 20000


def _chunks(items: List[str], size: int) -> Generator[List[str], None, None]:
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def mark_status_complete(
    client: bigquery.Client,
    status_table_id: str,
    guids: List[str],
    extraction_version: str,
) -> int:
    """Mark guids as successfully extracted.

    Returns:
        Number of rows updated
    """
    if isinstance(guids, str):
        guids = [guids]
    if not guids:
        return 0

    updated = 0
    for chunk in _chunks(guids, MARK_CHUNK):
        job = client.query(
            f"""
            UPDATE `{status_table_id}`
            SET status = 'complete',
                extraction_version = @version,
                error_message = NULL,
                extracted_at = CURRENT_TIMESTAMP()
            WHERE guid IN UNNEST(@guids)
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ArrayQueryParameter("guids", "STRING", chunk),
                bigquery.ScalarQueryParameter("version", "STRING", extraction_version),
            ]),
        )
        job.result()
        updated += job.num_dml_affected_rows or 0
    return updated


def mark_status_error(
    client: bigquery.Client,
    status_table_id: str,
    guids: List[str],
    error_type: str,
    error_message: str,
) -> int:
    """Park guids in an error status, out of the pending set.

    Parked rather than left pending on purpose. The queue is ordered largest
    first, so a document that fails deterministically would return to the front
    of every later fetch and the run would spin on it. It comes back only when
    requeue_status() is called at the start of a new run.

    Returns:
        Number of rows updated
    """
    if isinstance(guids, str):
        guids = [guids]
    if not guids:
        return 0

    updated = 0
    for chunk in _chunks(guids, MARK_CHUNK):
        job = client.query(
            f"""
            UPDATE `{status_table_id}`
            SET status = @status,
                error_message = @message,
                extracted_at = CURRENT_TIMESTAMP()
            WHERE guid IN UNNEST(@guids)
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ArrayQueryParameter("guids", "STRING", chunk),
                bigquery.ScalarQueryParameter("status", "STRING", f"error_{error_type}"),
                bigquery.ScalarQueryParameter("message", "STRING", error_message[:8192]),
            ]),
        )
        job.result()
        updated += job.num_dml_affected_rows or 0
    return updated


# Retry shape taken from mosaic-glean-extraction's bigquery_service.py.
# Four attempts at fixed 5s/20s/60s steps, no jitter: these wrap whole-query
# units (a metadata stream, a bin's body fetch), so a retry re-issues the query
# from scratch and the cost of an early retry is high enough that backing off
# hard beats backing off often.
RETRY_ATTEMPTS = 4
RETRY_BACKOFF_SEC = (5, 20, 60)

# Errors that mean the request itself is wrong, so repeating it cannot help.
PERMANENT_ERRORS = (
    gexc.BadRequest, gexc.NotFound, gexc.Forbidden,
    gexc.Unauthorized, gexc.Conflict,
)

# ...except when the message says otherwise. BigQuery reports both of these as
# BadRequest even though both clear on their own: a table's streaming buffer
# blocks DML for a few minutes after a load, and concurrent DML against one
# table surfaces as a serialization complaint rather than a rate limit.
TRANSIENT_MESSAGE_HINTS = ("streaming buffer", "concurrent update")


def retry_bq(what: str, fn, max_retries: int = RETRY_ATTEMPTS):
    """Retry a BigQuery operation, backing off on anything that might clear.

    Unknown exceptions are retried rather than raised. A corpus run holds one
    client open for hours, so a dropped connection or a DNS blip arrives as some
    ordinary exception rather than a google.api_core type — failing the whole
    run on the first of those would strand the remaining backlog.
    """
    import time

    def _delay(attempt: int) -> float:
        return RETRY_BACKOFF_SEC[min(attempt - 1, len(RETRY_BACKOFF_SEC) - 1)]

    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except PERMANENT_ERRORS as e:
            if not any(hint in str(e) for hint in TRANSIENT_MESSAGE_HINTS):
                logger.error(f"{what} failed permanently (not retryable): {e}")
                raise
            if attempt == max_retries:
                logger.error(f"{what} failed after {attempt} attempt(s): {e}")
                raise
            delay = _delay(attempt)
            logger.warning(f"{what} hit a transient condition "
                           f"(attempt {attempt}/{max_retries}): {e}. Retrying in {delay}s.")
            time.sleep(delay)
        except Exception as e:
            if attempt == max_retries:
                logger.error(f"{what} failed after {attempt} attempt(s): {e}")
                raise
            delay = _delay(attempt)
            logger.warning(f"{what} failed (attempt {attempt}/{max_retries}): {e}. "
                           f"Retrying in {delay}s.")
            time.sleep(delay)


def count_pending_guids(
    client: bigquery.Client,
    status_table_id: str,
    source: Optional[str] = None,
    guids: Optional[List[str]] = None,
) -> int:
    """Count pending guids, optionally restricted to a specific set.

    The restricted form is the reconciliation gate: before a fully-drained work
    queue is rebuilt, its own guids must already have left 'pending'. Without
    that check a rebuild re-claims work the previous queue just finished.
    """
    where = ["status = 'pending'"]
    params = []
    if source:
        where.append("source = @source")
        params.append(bigquery.ScalarQueryParameter("source", "STRING", source))
    if guids is not None:
        where.append("guid IN UNNEST(@guids)")
        params.append(bigquery.ArrayQueryParameter("guids", "STRING", list(guids)))

    query = f"""
    SELECT COUNT(*) AS n FROM `{status_table_id}`
    WHERE {' AND '.join(where)}
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params) if params else None
    return list(client.query(query, job_config=job_config).result())[0].n


def requeue_status(
    client: bigquery.Client,
    status_table_id: str,
    from_status: str,
    source: Optional[str] = None,
    limit: Optional[int] = None,
) -> int:
    """Put previously-failed guids back to 'pending'.

    Call at the START of a run, never inside the batch loop. A failure that is
    deterministic — a document the parser cannot handle at all — would otherwise
    be re-fetched immediately and forever: the queue is ordered by size, so the
    same document returns to the front of every subsequent fetch and the
    pipeline spins on it instead of making progress. Parking it in an error
    status and requeueing only on an explicit new run is what stops that.

    Returns:
        Number of rows moved back to pending
    """
    where = ["status = @from_status"]
    params = [bigquery.ScalarQueryParameter("from_status", "STRING", from_status)]
    if source:
        where.append("source = @source")
        params.append(bigquery.ScalarQueryParameter("source", "STRING", source))
    clause = " AND ".join(where)

    # BigQuery has no UPDATE ... LIMIT; scope through a subquery instead.
    if limit:
        clause = (
            f"guid IN (SELECT guid FROM `{status_table_id}` "
            f"WHERE {clause} ORDER BY extracted_at LIMIT {int(limit)})"
        )

    job = client.query(
        f"""
        UPDATE `{status_table_id}`
        SET status = 'pending', error_message = NULL, extracted_at = CURRENT_TIMESTAMP()
        WHERE {clause}
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    )
    job.result()
    moved = job.num_dml_affected_rows or 0
    logger.info(f"Requeued {moved} guid(s) from '{from_status}' to 'pending'")
    return moved
