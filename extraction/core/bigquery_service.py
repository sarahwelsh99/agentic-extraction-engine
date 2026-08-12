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
    ]

    table = bigquery.Table(table_id, schema=schema)
    table.clustering_fields = ["status"]

    try:
        table = client.create_table(table, exists_ok=True)
        logger.info(f"Status table {table_id} ready")
    except Exception as e:
        logger.error(f"Failed to create status table: {e}")
        raise


def fetch_pending_metadata(
    client: bigquery.Client,
    status_table_id: str,
    min_body_length: int = 50
) -> Generator[Tuple[str, int], None, None]:
    """Stream (guid, body_length) for every pending guid, largest first.

    No body_text, so this is cheap even across a multi-million-row backlog.
    Used for building local work queue before execution.
    """
    query = f"""
    SELECT guid, body_length
    FROM `{status_table_id}`
    WHERE status = 'pending'
      AND body_text IS NOT NULL
      AND body_length >= {min_body_length}
    ORDER BY body_length DESC
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


def mark_status_complete(
    client: bigquery.Client,
    status_table_id: str,
    guid: str,
    extraction_version: str
) -> None:
    """Mark a guid as successfully extracted."""
    query = f"""
    UPDATE `{status_table_id}`
    SET status = 'complete',
        extraction_version = @version,
        extracted_at = CURRENT_TIMESTAMP()
    WHERE guid = @guid
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("guid", "STRING", guid),
        bigquery.ScalarQueryParameter("version", "STRING", extraction_version),
    ])
    client.query(query, job_config=job_config).result()


def mark_status_error(
    client: bigquery.Client,
    status_table_id: str,
    guid: str,
    error_type: str,
    error_message: str
) -> None:
    """Mark a guid with an error status."""
    query = f"""
    UPDATE `{status_table_id}`
    SET status = @status,
        error_message = @message,
        extracted_at = CURRENT_TIMESTAMP()
    WHERE guid = @guid
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("guid", "STRING", guid),
        bigquery.ScalarQueryParameter("status", "STRING", f"error_{error_type}"),
        bigquery.ScalarQueryParameter("message", "STRING", error_message),
    ])
    client.query(query, job_config=job_config).result()


def retry_bq(what: str, fn, max_retries: int = config.BQ_MAX_RETRIES):
    """Retry BigQuery operations with exponential backoff.

    Retries on transient failures (rate limit, temporary unavailable).
    Fails fast on permanent errors (auth, schema, not found).
    """
    import time
    backoffs = [1, 2, 5, 10, 30]

    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except gexc.BadRequest as e:
            # Permanent error (schema, syntax, etc.)
            logger.error(f"{what} failed permanently: {e}")
            raise
        except (gexc.TooManyRequests, gexc.ServiceUnavailable, gexc.DeadlineExceeded) as e:
            # Transient error
            if attempt == max_retries:
                logger.error(f"{what} failed after {attempt} attempts: {e}")
                raise
            delay = backoffs[min(attempt - 1, len(backoffs) - 1)]
            logger.warning(f"{what} failed (attempt {attempt}/{max_retries}): {e}. "
                          f"Retrying in {delay}s.")
            time.sleep(delay)
        except Exception as e:
            logger.error(f"{what} failed unexpectedly: {e}")
            raise


def count_pending_guids(client: bigquery.Client, status_table_id: str) -> int:
    """Count number of pending guids."""
    query = f"SELECT COUNT(*) as count FROM `{status_table_id}` WHERE status = 'pending'"
    result = client.query(query).result()
    return list(result)[0].count
