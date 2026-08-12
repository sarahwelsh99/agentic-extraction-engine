"""Configuration for agentic extraction pipeline.

Adapted from mosaic-glean-extraction's config.py.
All values can be overridden via environment variables.
"""
import os

# ===== Project & Infrastructure =====
PROJECT_ID = os.getenv("PROJECT_ID", "")
DATASET_ID = os.getenv("DATASET_ID", "pii_extraction")
GCS_OUTPUT_BUCKET = os.getenv("GCS_OUTPUT_BUCKET", "")
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
QUEUE_TARGET_BIN_GUIDS = int(os.getenv("QUEUE_TARGET_BIN_GUIDS", "15000"))
QUEUE_DB_DIR = os.getenv("QUEUE_DB_DIR", "/tmp/extraction_queues")
FETCH_BATCH_SIZE = int(os.getenv("FETCH_BATCH_SIZE", "500"))
FETCH_MAX_BODY_BYTES = int(os.getenv("FETCH_MAX_BODY_BYTES", "500000000"))  # 500 MB
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "96"))
WRITE_BATCH_SIZE = int(os.getenv("WRITE_BATCH_SIZE", "1000"))
WRITE_BATCH_BYTES = int(os.getenv("WRITE_BATCH_BYTES", "10000000"))  # 10 MB

# ===== Schema Configuration =====
SCHEMA_VERSION = os.getenv("SCHEMA_VERSION", "1.0")
SCHEMA_DIR = os.getenv("SCHEMA_DIR", "extraction/schemas")

# Target fields for extraction (matches mosaic's PII schema)
SCHEMA_FIELDS = [
    "PERSON_EMAIL",
    "PERSON_PHONE",
    "PERSON_NAME",
    "PERSON_ADDRESS",
    "PERSON_CITY",
    "PERSON_STATE",
    "PERSON_ZIP",
    "PERSON_COUNTRY",
    "PERSON_DATE_OF_BIRTH",
    "PERSON_TAX_ID",
    "PERSON_DRIVER_LICENSE",
    "PERSON_PASSPORT",
    "PASSWORD_PIN",
    "JOB_TITLE",
    "OTHER_PII_TYPES",
]

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
