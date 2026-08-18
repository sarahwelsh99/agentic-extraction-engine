"""Configuration for agentic extraction pipeline.

Adapted from mosaic-glean-extraction's config.py.
All values can be overridden via environment variables.
"""
import os

# ===== Project & Infrastructure =====
PROJECT_ID = os.getenv("PROJECT_ID", "")
DATASET_ID = os.getenv("DATASET_ID", "glean_extract")
GCS_OUTPUT_BUCKET = os.getenv("GCS_OUTPUT_BUCKET", "glean-structured-agent-extraction")
GCS_OUTPUT_PREFIX = os.getenv("GCS_OUTPUT_PREFIX", "extraction")
GCS_LEDGER_PREFIX = os.getenv("GCS_LEDGER_PREFIX", "extraction-status-ledger")
GCS_ARTIFACTS_PREFIX = os.getenv("GCS_ARTIFACTS_PREFIX", "extraction-artifacts")
GCS_INPUT_PREFIX = os.getenv("GCS_INPUT_PREFIX", "extraction-input")

# ===== Data Source Configuration =====
# Query source: glean.drive_files where triage_category = 'INCL_STRUCTURED_RECORD'
SOURCE_PROJECT = os.getenv("SOURCE_PROJECT", "glean")
SOURCE_TABLE = os.getenv("SOURCE_TABLE", "drive_files")
SOURCE_TRIAGE_CATEGORY = os.getenv("SOURCE_TRIAGE_CATEGORY", "INCL_STRUCTURED_RECORD")
SOURCE_TABLE_NAME = os.getenv("SOURCE_TABLE_NAME", "agentic_extraction_status")
ACTIVE_SOURCE_FILE = os.getenv("ACTIVE_SOURCE_FILE", "/tmp/agentic_extraction.source")

# ===== Local vLLM Configuration =====
VLLM_API_BASE = os.getenv("VLLM_API_BASE", "http://localhost:8000")
VLLM_MODEL = os.getenv("VLLM_MODEL", "QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8")
VLLM_TIMEOUT = int(os.getenv("VLLM_TIMEOUT", "300"))
VLLM_MAX_RETRIES = int(os.getenv("VLLM_MAX_RETRIES", "3"))

# ===== Pipeline Agent (Looker -> Thinker -> Tester -> Eval) =====
# Attempts at generating a working script, including the first. The single
# source of truth for the retry ceiling: extraction/core/pipeline_agent.py and
# tools/evaluate_extraction/tool.py both read this rather than each keeping
# their own copy, which previously had to be kept in sync by hand.
MAX_EXTRACTION_ATTEMPTS = int(os.getenv("MAX_EXTRACTION_ATTEMPTS", "2"))

# ===== Phase Configuration =====
# Phase 1: Pattern Analysis & Code Generation
PHASE1_SAMPLES_PER_SOURCE = int(os.getenv("PHASE1_SAMPLES_PER_SOURCE", "20"))
PHASE1_MAX_SAMPLE_SIZE_KB = int(os.getenv("PHASE1_MAX_SAMPLE_SIZE_KB", "500"))

# Phase 2: Safety Validation & Testing
PHASE2_SAFETY_CHECK_ENABLED = os.getenv("PHASE2_SAFETY_CHECK_ENABLED", "true").lower() == "true"
PHASE2_TEST_SAMPLES = int(os.getenv("PHASE2_TEST_SAMPLES", "50"))
PHASE2_SCHEMA_VALIDATION = os.getenv("PHASE2_SCHEMA_VALIDATION", "true").lower() == "true"

# Phase 3: Quality Feedback Loop
PHASE3_QUALITY_SAMPLE_SIZE = int(os.getenv("PHASE3_QUALITY_SAMPLE_SIZE", "10000"))
PHASE3_MAX_ITERATIONS = int(os.getenv("PHASE3_MAX_ITERATIONS", "3"))
PHASE3_QUALITY_THRESHOLD = float(os.getenv("PHASE3_QUALITY_THRESHOLD", "0.85"))

# Phase 4: Deterministic Execution
QUEUE_MODE = os.getenv("QUEUE_MODE", "1") == "1"
FETCH_BATCH_SIZE = int(os.getenv("FETCH_BATCH_SIZE", "500"))

# A bin is three things at once: the unit prefetched from BigQuery, the unit
# loaded in one BigQuery job, and the checkpoint a crash resumes from. Tied to
# FETCH_BATCH_SIZE the way mosaic ties them, but at our value rather than
# mosaic's 15,000: their unit of work is one LLM call, ours runs a container per
# document, so a bin takes long enough that checkpointing 15,000 documents apart
# would mean losing hours of work to one crash.
QUEUE_TARGET_BIN_GUIDS = int(os.getenv("QUEUE_TARGET_BIN_GUIDS", str(FETCH_BATCH_SIZE)))
QUEUE_DB_DIR = os.getenv("QUEUE_DB_DIR", "/tmp/extraction_queues")
FETCH_MAX_BODY_BYTES = int(os.getenv("FETCH_MAX_BODY_BYTES", "500000000"))  # 500 MB

# Documents processed concurrently. Deliberately not mosaic's 96: there a worker
# waits on an LLM HTTP call and is I/O-bound, so oversubscribing cores is free.
# Here a worker spawns a Docker container pinned to --cpus 1.0, so the pool is
# CPU-bound and workers beyond the core count just make every container slower.
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "32"))
WRITE_BATCH_SIZE = int(os.getenv("WRITE_BATCH_SIZE", "1000"))
WRITE_BATCH_BYTES = int(os.getenv("WRITE_BATCH_BYTES", "10000000"))  # 10 MB

# ===== Schema Configuration =====
SCHEMA_VERSION = os.getenv("SCHEMA_VERSION", "1.0")
SCHEMA_DIR = os.getenv("SCHEMA_DIR", "extraction/schemas")
SCHEMA_FILE = os.getenv("SCHEMA_FILE", "extraction/schemas/mosaic_pii_schema.json")

# Target fields for extraction - EXACT SAME SCHEMA as mosaic-glean-extraction
# See: https://github.com/sarahwelsh99/mosaic-glean-extraction/blob/main/extraction/config.py
SCHEMA_FIELDS = [
    "RECORD_TYPE", "JURISDICTION", "TELUS_BUSINESS",
    "COMPANY_NAME", "DOCUMENT_CLASSIFICATION", "BOOL_PERSONAL_DATA", "PERSON_FULL_NAME",
    "PERSON_FIRST_NAME", "PERSON_MIDDLE_NAME", "PERSON_LAST_NAME", "PERSON_SUFFIX",
    "PERSON_ID_TYPE", "PERSON_ID", "PERSON_EMAIL", "PERSON_PHONE_NUM", "PHONE_ID",
    "PERSON_DATE_OF_BIRTH", "PERSON_TAX_ID", "PERSON_ADDRESS_FULL", "PERSON_ADDRESS_STREET",
    "PERSON_ADDRESS_LINE2", "PERSON_ADDRESS_CITY", "PERSON_ADDRESS_STATE", "PERSON_ADDRESS_ZIP",
    "PERSON_ADDRESS_COUNTRY", "FULL_CC_NUM", "CC_CVV", "CC_EXPIRATION", "DRIVERS_LICENSE",
    "PASSPORT", "MILITARY_ID", "GOVERNMENT_ID", "BANK_ACCT_NUM", "BANK_ROUTING_NUM",
    "GEOLOCATION", "PATIENT_ID_TYPE", "PATIENT_ID", "IMEI_NUM", "IMSI_NUM", "E_SIM_SIM_EZ",
    "BOOL_EMPLOYEE_COMPENSATION", "BOOL_BIOMETRIC_DATA", "BOOL_DIGITAL_SIGNATURE",
    "BOOL_PERSONAL_CHARACTERISTICS", "BOOL_END_USER_CONTRACT", "BOOL_PATIENT_HISTORY",
    "PASSWORD_PIN",
    "JOB_TITLE",
    "OTHER_PII_TYPES",
]

# Field-name aliases: LLM sometimes emits values under natural-language keys
# (e.g. "SSN" instead of PERSON_TAX_ID). These are rerouted to canonical columns.
# This prevents off-schema keys and improves dedup reliability.
SCHEMA_ALIASES = {
    "ssn": "PERSON_TAX_ID",
    "social_security_number": "PERSON_TAX_ID",
    "tin": "PERSON_TAX_ID",
    "tax_id": "PERSON_TAX_ID",
    "dob": "PERSON_DATE_OF_BIRTH",
    "date_of_birth": "PERSON_DATE_OF_BIRTH",
    "birth_date": "PERSON_DATE_OF_BIRTH",
    "birthdate": "PERSON_DATE_OF_BIRTH",
    "email": "PERSON_EMAIL",
    "email_address": "PERSON_EMAIL",
    "phone": "PERSON_PHONE_NUM",
    "phone_number": "PERSON_PHONE_NUM",
    "name": "PERSON_FULL_NAME",
    "full_name": "PERSON_FULL_NAME",
    "address": "PERSON_ADDRESS_FULL",
    "address_full": "PERSON_ADDRESS_FULL",
    "person_address_province": "PERSON_ADDRESS_STATE",
    "province": "PERSON_ADDRESS_STATE",
    "country": "PERSON_ADDRESS_COUNTRY",
    "pin": "PASSWORD_PIN",
    "password": "PASSWORD_PIN",
    "passcode": "PASSWORD_PIN",
    "temporary_passcode": "PASSWORD_PIN",
    "token": "PASSWORD_PIN",
    "security_code": "PASSWORD_PIN",
    "rsa_pin": "PASSWORD_PIN",
    "job_title": "JOB_TITLE",
    "title": "JOB_TITLE",
    "role": "JOB_TITLE",
    "position": "JOB_TITLE",
    "designation": "JOB_TITLE",
}

# ===== Logging & Debugging =====
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# ===== BigQuery & GCS Retry Configuration =====
BQ_TIMEOUT = int(os.getenv("BQ_TIMEOUT", "300"))
BQ_MAX_RETRIES = int(os.getenv("BQ_MAX_RETRIES", "5"))
GCS_TIMEOUT = int(os.getenv("GCS_TIMEOUT", "60"))
GCS_MAX_RETRIES = int(os.getenv("GCS_MAX_RETRIES", "3"))

def validate_config():
    """Check that required config values are set."""
    required = ["PROJECT_ID", "GCS_OUTPUT_BUCKET"]
    missing = [k for k in required if not globals()[k]]
    if missing:
        raise ValueError(f"Missing required config: {missing}")
