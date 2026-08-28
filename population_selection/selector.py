"""Population selection: decide which triage-matched documents get extracted.

Deliberately independent of orchestrator.py, run_pipeline.py, phase1-4, and
tools/ -- this only needs BigQuery and the shared config, so it can run
before any of the rest of the pipeline exists and be rerun on its own
schedule.

One set-based MERGE, no per-row round trips and no LLM calls at all: scores
each triage-matched source document's body_text against PII_CATEGORY_PATTERNS
and flags it 'pending' (needs extraction) the moment ANY category matches,
'excluded_no_pii' when none do.

The category patterns and this any-match rule are ported directly from
mosaic-glean-extraction's extraction/prefilter.py (Shubhankar Dash) rather
than invented here -- that module is the production-validated version of
exactly this decision, already backtested against real drive_files
extraction ground truth (see mosaic-glean-extraction's
tests/backtest_prefilter.py and its config.py PREFILTER_ENABLED comment: a
documented, accepted 3.7% false-negative rate on drive specifically).
Independently re-running that same backtest against 20,000 real
glean.drive_files guids with real pii_extraction ground truth in this
project measured a 77.7% skip rate at a 1.85% false-negative rate among
skipped docs -- most of which were PERSON_DATE_OF_BIRTH-only hits, which
Dash's own notes document as the source model hallucinating ordinary
business dates as DOB on drive documents (i.e. noise in the ground truth
itself, not real misses).

Deliberately NOT required: a person's name. A bare name, or a name plus only
contact info (email/phone), is not on its own treated as notifiable here --
see PII_CATEGORY_PATTERNS' docstring for the full reasoning ported from
prefilter.py. This is a real design choice with compliance implications, not
a detail to silently change.

Rerunning is safe: only rows still sitting in pending/excluded_no_pii (or the
now-retired needs_llm_review, kept in the MATCHED guard so any row left in
that state by an earlier version of this module still gets reclassified) are
touched, so anything Phase 4 has already completed or errored on is left
alone.
"""
import logging
import re
from typing import Dict, Optional

from google.cloud import bigquery

from extraction.core import config

logger = logging.getLogger(__name__)

# Each pattern is checked against a document's whole body_text. Case
# insensitive; deliberately liberal, since a false positive here only costs
# one ordinary extraction run while a false negative permanently excludes a
# document that might carry real PII. Ported verbatim from
# mosaic-glean-extraction's extraction/prefilter.py (_CATEGORY_PATTERNS),
# which is itself the product of real-traffic backtesting (see that file's
# module docstring for the false negatives -- a Bulgarian DOB with no English
# keyword, a European street-first address, a telecom "Mobility Account"
# field -- that shaped these patterns). Do not narrow these to reduce match
# volume; only broaden in response to a documented backtest gap, same rule
# prefilter.py itself follows.
#
# No "name" category, on purpose: a bare person name, or a name plus only
# contact info (email/phone), is not treated as notifiable PII by itself
# under this pipeline's compliance framing -- what makes a document
# notifiable is a sensitive identifying/financial/health/credential
# attribute being present at all, regardless of whether a name sits next to
# it. Any single category match below is sufficient on its own.
PII_CATEGORY_PATTERNS = {
    "DOB": (
        r"(?i)\b(date\s+of\s+birth|d\.?\s*o\.?\s*b\.?|birth\s*date|birthdate|"
        r"born\s+on|year\s+of\s+birth|"
        r"fecha\s+de\s+nacimiento|date\s+de\s+naissance|n[eé]\(?e?\)?\s+le|"
        r"geburtsdatum|geburtstag|geboren\s+am|"
        r"data\s+di\s+nascita|data\s+de\s+nascimento|"
        r"дата\s+на\s+раждане)\b|"
        r"\bdate\b[^\n.]{0,25}\bbirth\b|"
        r"\*\s*\d{1,2}[./]\d{1,2}[./]\d{2,4}"
    ),
    "GOVERNMENT_ID": (
        r"(?i)(\b\d{3}[-\s]\d{2}[-\s]\d{4}\b|\b\d{3}[-\s]\d{3}[-\s]\d{3}\b|"
        r"\b\d{4}[-\s]\d{4}[-\s]\d{4}\b|\b\d{12}\b|"
        r"\b[A-Z]{5}\d{4}[A-Z]\b|\b[A-Z]{2}\d{6}[A-Z]\b|"
        r"\bssn\b|\bsin\b|social\s+insurance\s+number|social\s+security\s+number|"
        r"passport(?:\s+(?:no|number|#))?|driver'?s?\s+licen[cs]e|"
        r"military\s+id|national\s+id(?:entity)?(?:\s+(?:no|number|card))?|"
        r"government[\s-]issued\s+id|"
        r"aadhaa?r(?:\s+(?:no|number|card))?|\buidai\b|"
        r"\bpan\s+(?:card|number|no)\b|permanent\s+account\s+number|"
        r"national\s+insurance\s+number|\bnino\b|"
        r"\bcurp\b|\brfc\b|tax\s+identification\s+number|\btin\s+(?:no|number)\b|"
        r"documento\s+de\s+identidad|documento\s+nacional\s+de\s+identidad|\bdni\b|"
        r"\bcnp\b|"
        r"social\s+security\s+system|\bsss\s+number\b|ss\s+number\s+slip)"
    ),
    # Original has (?=\W|$) lookaheads to end each street-type alternative --
    # RE2 (BigQuery's REGEXP_CONTAINS engine) has no lookahead support, unlike
    # Python's re. run_regex_pass() rewrites this one category's pattern via
    # _to_re2() (swaps (?=\W|$) for the consuming (?:\W|$), which is
    # equivalent for a presence check) before it reaches SQL; classify_text
    # uses this exact string with Python's re, matching prefilter.py verbatim.
    "ADDRESS": (
        r"(?i)("
        r"\b\d{1,6}\s+\w+(?:\s\w+){0,3}\s+"
        r"(?:street|st\.?|avenue|ave\.?|road|rd\.?|boulevard|blvd\.?|drive|dr\.?|"
        r"lane|ln\.?|way|court|ct\.?|place|pl\.?|suite|ste\.?|parkway|pkwy\.?|"
        r"circle|cir\.?|highway|hwy\.?|terrace|terr\.?|square|sq\.?|crescent|cres\.?)(?=\W|$)|"
        r"\b(?:av(?:e|da)?\.?|avenida|avenue|calle|carrer|rua|str\.|"
        r"stra(?:ß|ss)e|straat|rue|via)(?=\W|$)[^\n.]{0,40}\d{1,6}|"
        r"\d{1,6}[^\n,.]{0,40}\b(?:stra(?:ß|ss)e|straat)\b|"
        r"\b[A-ZÄÖÜ][\wäöüßÀ-ÿ-]*(?:weg|gasse|platz|ring|allee)\b[^\n.]{0,40}\d{1,6}|"
        r"\bmailing\s+address\b|\bhome\s+address\b|\bresiden(?:ce|tial)\s+address\b|"
        r"\baddress\s*:|\bcalea\b|\bplz\s*:|"
        r"\bdirecci[oó]n\b|\badresse\b|\bindirizzo\b|"
        r"\b\d{4,6}(?:-\d{3,4})?\s+[A-Z][\wÀ-ÿ]+[.,]?\s+[A-Z][\wÀ-ÿ]+\.|"
        r",\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b|"
        r",\s*[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\s+\d{5}(?:-\d{4})?\b|"
        r"\b[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d\b)"
    ),
    "FINANCIAL_ACCOUNT": (
        r"(?i)(\b(?:\d[ -]?){13,19}\b|\brouting\s+number\b|\bbank\s+account\b|"
        r"\baccount\s+number\b|\biban\b|\bswift\s+(?:code|number)?\b|"
        r"\bcvv\b|\bcvc\b|\bexpiration\s+date\b.{0,20}\bcard\b|"
        r"\b\w*account\b[^\n]{0,20}\d{5,}|\bsubscriber\s+number\b[^\n]{0,20}\d{3,}|"
        r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b)"
    ),
    "HEALTH_OR_BIOMETRIC": (
        r"(?i)(\bpatient\s+id\b|\bmedical\s+record(?:\s+number)?\b|\bmrn\b|"
        r"\bdiagnos(?:is|ed)\b|\bbiometric\b|\bfingerprint\b|"
        r"\bfacial\s+recognition\b|\bretina\s+scan\b|\bpatient\s+history\b|"
        r"\bmedical\s+certificate\b)"
    ),
    "CREDENTIAL": (
        r"(?i)(\bpassword\b|\bpasscode\b|\bpin\s*(?:code|number)?\s*[:=]|"
        r"\bsecurity\s+code\b|\bone[-\s]time\s+(?:code|passcode|password)\b)"
    ),
    "DEVICE_ID": r"(?i)(\bimei\b|\bimsi\b|\biccid\b|\be[-\s]?sim\b|\b\d{15}\b)",
    "PERSON_ID": r"(?i)\b(?:customer|member|client|loyalty|reference)\s*(?:id|number|no)\b[^\n]{0,20}\d{3,}",
    "PERSONAL_EMAIL": (
        r"(?i)[\w.+-]+@(?:gmail\.com|googlemail\.com|yahoo\.[a-z.]{2,10}|"
        r"outlook\.(?:com|[a-z]{2,3})|hotmail\.[a-z.]{2,10}|live\.[a-z.]{2,10}|"
        r"icloud\.com|me\.com|mac\.com|protonmail\.(?:com|ch)|proton\.me|"
        r"aol\.com|msn\.com|mail\.com|gmx\.(?:com|net|de|at)|"
        r"yandex\.(?:com|ru)|web\.de|qq\.com|163\.com|126\.com|naver\.com|"
        r"rediffmail\.com|zoho\.com)\b"
    ),
}

_TABLE_SCHEMA = [
    bigquery.SchemaField("guid", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("extraction_version", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("extracted_at", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("error_message", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("body_length", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("body_text", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("pii_score", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("pii_signals", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("pii_detection_method", "STRING", mode="NULLABLE"),
    # Which population a row belongs to. Every runtime read in
    # extraction/core/bigquery_service.py filters on it, so a row without it is
    # invisible to a source-scoped run.
    bigquery.SchemaField("source", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("gpu_machine", "STRING", mode="NULLABLE"),
]


def get_bigquery_client() -> bigquery.Client:
    """Own client factory -- kept local so this module never has to import
    extraction.core.bigquery_service (Phase 4's execution infra)."""
    return bigquery.Client(project=config.PROJECT_ID)


def get_status_table_id() -> str:
    return f"{config.PROJECT_ID}.{config.DATASET_ID}.{config.SOURCE_TABLE_NAME}"


def ensure_status_table(client: bigquery.Client, table_id: str) -> None:
    """Create the shared status table if this is the first module to touch
    it, or add this module's columns if bigquery_service.py already created
    it with its own (narrower) schema. Each owns its own migration so
    neither has to import the other.
    """
    table = bigquery.Table(table_id, schema=_TABLE_SCHEMA)
    table.clustering_fields = ["status"]
    client.create_table(table, exists_ok=True)
    client.query(f"""
        ALTER TABLE `{table_id}`
        ADD COLUMN IF NOT EXISTS pii_score INT64,
        ADD COLUMN IF NOT EXISTS pii_signals STRING,
        ADD COLUMN IF NOT EXISTS pii_detection_method STRING,
        ADD COLUMN IF NOT EXISTS source STRING,
        ADD COLUMN IF NOT EXISTS gpu_machine STRING
    """).result()


def _to_re2(pattern: str) -> str:
    """Make a Python-flavored pattern safe for BigQuery's RE2 engine.

    Only transformation needed for these patterns: RE2 has no lookahead, so
    ADDRESS's (?=\\W|$) (asserts a boundary without consuming it) becomes
    (?:\\W|$) (matches a boundary, consuming it). Equivalent for a presence
    check (REGEXP_CONTAINS), since consuming the boundary character doesn't
    change whether a match exists anywhere in the string.
    """
    return pattern.replace(r"(?=\W|$)", r"(?:\W|$)")


def classify_text(body_text: str) -> dict:
    """Pure-Python mirror of the MERGE query's REGEXP_CONTAINS scoring, using
    the same patterns prefilter.py runs with Python's re (not the RE2-safe
    SQL variant) -- exists so the categories can be exercised in unit tests
    without a live BigQuery connection.
    """
    signals = {name: bool(re.search(pattern, body_text or "")) for name, pattern in PII_CATEGORY_PATTERNS.items()}
    matched_names = [name for name, matched in signals.items() if matched]
    return {
        "pii_score": sum(signals.values()),
        "has_pii": any(signals.values()),
        "pii_signals": ",".join(matched_names),
    }


def run_regex_pass(
    client: bigquery.Client,
    status_table_id: str,
    source_project: str = config.SOURCE_PROJECT,
    source_table: str = config.SOURCE_TABLE,
    triage_category: str = config.SOURCE_TRIAGE_CATEGORY,
    limit: Optional[int] = None,
) -> Dict[str, int]:
    """Score every triage-matched source document for PII and MERGE the result in.

    One set-based MERGE -- no per-row BigQuery round trip, no LLM calls.
    `limit` caps how many source rows are scored, for smoke-testing on a
    slice before committing to a full-table run.

    body_text is stored as "Document: {title}\\n\\n{body_text}" (title
    concatenated on, same as a real title-carrying value) when the source row
    has a non-blank title, matching mosaic-glean-extraction's
    initialize_status_table title-concatenation for its drive_files
    _POPULATE_RULES entry (title_label="Document") -- this pipeline's status
    table is organized the same way. PII categories are scored against that
    same concatenated text, not just the raw body, since a title can carry
    PII on its own (e.g. a person's name) and that's the exact text
    downstream extraction will see. body_length is passed through from the
    source table's own precomputed column, also matching mosaic -- it
    reflects the original body_text's size, not the stored (title-prefixed)
    value's.
    """
    source_table_id = f"{source_project}.{source_table}"
    signal_cols = list(PII_CATEGORY_PATTERNS.items())
    has_cols_sql = ",\n      ".join(
        f"IF(REGEXP_CONTAINS(body_text, r'''{_to_re2(pattern)}'''), 1, 0) AS has_{name.lower()}"
        for name, pattern in signal_cols
    )
    score_sql = " + ".join(f"has_{name.lower()}" for name, _ in signal_cols)
    has_pii_sql = " OR ".join(f"has_{name.lower()} = 1" for name, _ in signal_cols)
    signals_array_sql = ", ".join(f"IF(has_{name.lower()}=1, '{name}', NULL)" for name, _ in signal_cols)
    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    query = f"""
    MERGE `{status_table_id}` T
    USING (
      WITH pop AS (
        SELECT
          guid,
          CASE WHEN title IS NOT NULL AND TRIM(title) != ''
               THEN CONCAT('Document: ', title, '\\n\\n', body_text)
               ELSE body_text
          END AS body_text,
          body_length
        FROM `{source_table_id}`
        WHERE triage_category = @triage_category
          AND body_text IS NOT NULL
          AND LENGTH(body_text) > 0
        {limit_sql}
      ),
      scored AS (
        SELECT
          guid, body_text, body_length,
          {has_cols_sql}
        FROM pop
      )
      SELECT
        guid, body_text, body_length,
        ({score_sql}) AS pii_score,
        ({has_pii_sql}) AS has_pii,
        ARRAY_TO_STRING([{signals_array_sql}], ',') AS pii_signals
      FROM scored
    ) S
    ON T.guid = S.guid
    WHEN MATCHED AND T.status IN ('pending', 'needs_llm_review', 'excluded_no_pii') THEN
      UPDATE SET
        status = IF(S.has_pii, 'pending', 'excluded_no_pii'),
        pii_score = S.pii_score,
        pii_signals = S.pii_signals,
        pii_detection_method = 'regex',
        body_length = S.body_length,
        body_text = S.body_text,
        source = @source
    WHEN NOT MATCHED THEN
      INSERT (guid, status, pii_score, pii_signals, pii_detection_method,
              body_length, body_text, source)
      VALUES (
        S.guid, IF(S.has_pii, 'pending', 'excluded_no_pii'), S.pii_score, S.pii_signals,
        'regex', S.body_length, S.body_text, @source
      )
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("triage_category", "STRING", triage_category),
        bigquery.ScalarQueryParameter("source", "STRING", source_table),
    ])
    job = client.query(query, job_config=job_config)
    job.result()
    affected = job.num_dml_affected_rows or 0
    logger.info(f"Population regex pass: {affected} rows scored from {source_table_id}")
    return {"rows_scored": affected}


def get_status_counts(client: bigquery.Client, status_table_id: str) -> Dict[str, int]:
    """Current row count per status, for reporting population state."""
    query = f"SELECT status, COUNT(*) AS n FROM `{status_table_id}` GROUP BY status"
    return {row.status: row.n for row in client.query(query).result()}


def select_population(
    client: Optional[bigquery.Client] = None,
    source_limit: Optional[int] = None,
) -> Dict[str, Dict[str, int]]:
    """Run population selection: one regex pass, no LLM calls.

    `source_limit` caps how many source rows are scored, for smoke-testing
    on a slice before committing to a full-table run.

    Safe to call repeatedly -- see module docstring.
    """
    client = client or get_bigquery_client()
    table_id = get_status_table_id()
    ensure_status_table(client, table_id)

    regex_counts = run_regex_pass(client, table_id, limit=source_limit)
    logger.info(f"Regex pass: {regex_counts}")

    return {"regex": regex_counts}
