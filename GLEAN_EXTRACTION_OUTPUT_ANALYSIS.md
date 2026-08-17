# How mosaic-glean-extraction Records Output

Analysis of the mosaic-glean-extraction repo's approach to storing/recording extraction results.

## Key Architectural Decision: Write to GCS First, Then Load to BigQuery

### Why NOT direct BigQuery writes?

From `output_store.py`:
> The pipeline no longer writes extracted rows to BigQuery. It writes NDJSON files to a bucket, and loading those into `pii_extraction` is a separate step run when you want it. That removes the whole class of problems the write path had:
> 
> - Streaming inserts (`insert_rows_json`) left rows in a streaming buffer for up to ~90 minutes, during which BigQuery refuses any DML touching them. The dedup DELETE therefore failed with a 400 "would affect rows in the streaming buffer" for any guid reprocessed soon after its last extraction.
> - Every write took a DML lock on `pii_extraction`, which backed up the writer queue and stalled result collection.
> - Per-flush DML cost seconds to minutes, which backed up the writer queue.

**Solution**: Write to GCS as NDJSON (newline-delimited JSON), then load asynchronously.

---

## Output Storage: GCS NDJSON Files

### Directory Structure
```
gs://<GCS_OUTPUT_BUCKET>/<GCS_OUTPUT_PREFIX>/
  source=<source_name>/
    dt=<YYYY-MM-DD>/
      run=<run_id>/
        batch-000001-part-00001.jsonl
        batch-000001-part-00002.jsonl
        batch-000002-part-00001.jsonl
        ...
```

**Partition Components:**
- `source=`: Hive-style partition (e.g., `drive_files`, `emails`, `chat_messages`)
- `dt=`: Date partition (YYYY-MM-DD) for easy filtering
- `run=`: Run ID to prevent collisions between concurrent/restarted runs
- `batch-<batch_id>-part-<seq>.jsonl`: Actual NDJSON files

### Why This Structure?

1. **Hive-style partitions** (`source=`, `dt=`) - Allow efficient BigQuery filtering on load
2. **Run ID** - Multiple machines can run extraction concurrently; run ID prevents file collisions
3. **Batch-based** - Results written as they come in, not batched for a single megafile
4. **NDJSON format** - One JSON object per line, easy to parse and stream-load

### File Format: NDJSON (Newline-Delimited JSON)

Each line is a complete extraction result for one person-row:

```json
{"guid":"ddffbdb6-5041-4d65-a744-5a0631a629aa","PERSON_FULL_NAME":"John Smith","PERSON_EMAIL":"john@company.com","PERSON_ID":"10001","PERSON_ID_TYPE":"EMPLOYEE_ID","EXTRACTED_AT":"2026-08-13T18:00:00Z","RAW_LLM_RESPONSE":"{...full LLM response...}","ERROR_MESSAGE":null,"prompt_version":"v2"}
{"guid":"ddffbdb6-5041-4d65-a744-5a0631a629aa","PERSON_FULL_NAME":"Jane Doe","PERSON_EMAIL":"jane@company.com","PERSON_ID":"10002","PERSON_ID_TYPE":"EMPLOYEE_ID","EXTRACTED_AT":"2026-08-13T18:00:00Z","RAW_LLM_RESPONSE":"{...}","ERROR_MESSAGE":null,"prompt_version":"v2"}
```

**Compact encoding**: Uses `separators=(",", ":")` to minimize file size (RAW_LLM_RESPONSE dominates).

---

## BigQuery Table: pii_extraction

### Table Structure

**Table ID**: `{PROJECT_ID}.glean_extract.pii_extraction`

**Columns:**

1. **Audit Columns**
   - `guid` (STRING, REQUIRED) - Document identifier
   - `EXTRACTED_AT` (TIMESTAMP, NULLABLE) - When extracted (denormalized across all person-rows for same guid)
   - `prompt_version` (STRING, NULLABLE) - Which prompt version produced this record (denormalized)
   - `ERROR_MESSAGE` (STRING, NULLABLE) - Error message if extraction failed
   - `RAW_LLM_RESPONSE` (STRING, NULLABLE) - Complete LLM output for this person-row
   - `qc_status` (STRING, NULLABLE) - QC annotation (populated by external QC process)

2. **PII Extraction Columns** (49 fields total)
   
   **Person Identifiers:**
   - `PERSON_FULL_NAME`, `PERSON_FIRST_NAME`, `PERSON_MIDDLE_NAME`, `PERSON_LAST_NAME`, `PERSON_SUFFIX`
   - `PERSON_ID`, `PERSON_ID_TYPE` (paired fields - ID + type)
   
   **Contact:**
   - `PERSON_EMAIL`, `PERSON_PHONE_NUM`, `PHONE_ID`
   
   **Personal:**
   - `PERSON_DATE_OF_BIRTH`, `PERSON_TAX_ID`
   
   **Address:**
   - `PERSON_ADDRESS_FULL`, `PERSON_ADDRESS_STREET`, `PERSON_ADDRESS_LINE2`
   - `PERSON_ADDRESS_CITY`, `PERSON_ADDRESS_STATE`, `PERSON_ADDRESS_ZIP`, `PERSON_ADDRESS_COUNTRY`
   
   **Payment:**
   - `FULL_CC_NUM`, `CC_CVV`, `CC_EXPIRATION`
   - `BANK_ACCT_NUM`, `BANK_ROUTING_NUM`
   
   **Identification:**
   - `DRIVERS_LICENSE`, `PASSPORT`, `MILITARY_ID`, `GOVERNMENT_ID`
   - `PATIENT_ID`, `PATIENT_ID_TYPE` (paired fields)
   - `IMEI_NUM`, `IMSI_NUM`, `E_SIM_SIM_EZ`
   
   **Metadata:**
   - `PERSON_FULL_NAME` (used as dedup key)
   
   **Document Classification Booleans:**
   - `BOOL_PERSONAL_DATA`, `BOOL_EMPLOYEE_COMPENSATION`, `BOOL_BIOMETRIC_DATA`
   - `BOOL_DIGITAL_SIGNATURE`, `BOOL_PERSONAL_CHARACTERISTICS`
   - `BOOL_END_USER_CONTRACT`, `BOOL_PATIENT_HISTORY`
   
   **Other:**
   - `GEOLOCATION`, `PASSWORD_PIN`, `JOB_TITLE`, `OTHER_PII_TYPES`
   - `RECORD_TYPE`, `JURISDICTION`, `TELUS_BUSINESS`, `COMPANY_NAME`, `DOCUMENT_CLASSIFICATION`

**All PII columns are STRING type and NULLABLE** (empty/missing values are NULL, not empty strings).

---

## Loading: GCS → BigQuery (load_gcs_to_bq.py)

### Two-Phase Loading Strategy

**Phase 1: Load to Staging Table**
```sql
LOAD DATA INTO staging_table
FROM 'gs://bucket/source=.../dt=.../run=.../*.jsonl'
FORMAT = NDJSON
```

**Phase 2: Merge Deduplication & Insert**

When a guid appears multiple times (reprocessed after error, or deliberate re-extraction), keep only the newest `EXTRACTED_AT`:

```sql
-- Delete stale rows for guids that have newer extractions in staging
DELETE FROM pii_extraction AS t
WHERE t.guid IN (
  SELECT sl.guid
  FROM (SELECT guid, MAX(EXTRACTED_AT) AS extracted_at FROM staging)
  LEFT JOIN (SELECT guid, MAX(EXTRACTED_AT) AS extracted_at FROM pii_extraction)
  WHERE new.extracted_at >= old.extracted_at  -- newer or equal
)

-- Insert winning row-sets
INSERT INTO pii_extraction (...)
SELECT * FROM staging
WHERE guid NOT IN (SELECT DISTINCT guid FROM pii_extraction)  -- new guids only
  AND EXTRACTED_AT = (SELECT MAX(EXTRACTED_AT) FROM staging s2 WHERE s2.guid = s.guid)
```

### Why Per-Guid Deduplication?

A single `guid` can span **multiple rows** (multiple person-rows extracted from one document):

```
guid=ABC123 → Person 1 (John Smith)
guid=ABC123 → Person 2 (Jane Doe)
guid=ABC123 → Person 3 (CEO Title)
```

If `guid=ABC123` is reprocessed, ALL its person-rows get a new `EXTRACTED_AT`. The loader:
1. Deletes the old set (3 rows with OLD_EXTRACTED_AT)
2. Inserts the new set (3 rows with NEW_EXTRACTED_AT)

**Cannot use simple MERGE** because MERGE matches 1:1 on the ON clause, but here we need "replace this guid's entire multi-row set."

### Handling Errors & Edge Cases

**If a guid appears with mixed results:**
- Some rows with valid JSON → good extraction
- Some rows with NULL RAW_LLM_RESPONSE → error during extraction
- Some rows with parsing errors → truncated/malformed response

The loader keeps all rows for that guid with the same `EXTRACTED_AT` (denormalized across the set). Status reconciliation happens separately via `bigquery_service.mark_status_*`.

---

## Status Tracking: Separate Table (pii_extraction_status)

The `pii_extraction` table contains **extraction results only**. Status tracking is separate in `pii_extraction_status`:

| status | meaning | pii_extraction row | RAW_LLM_RESPONSE |
|--------|---------|-------------------|------------------|
| `pending` | Not yet processed | None | N/A |
| `complete` | Successful extraction | ✅ YES | ✅ Valid JSON |
| `error_truncated` | Output token cap hit mid-chunk | ✅ YES (partial) | ⚠️ Truncated JSON |
| `error_llm` | LLM call failed | ✅ YES | ✅ NULL |
| `error_oversized` | Document too large, skipped | ✅ YES | ✅ NULL |
| `no_body` | No extractable text (chat only) | ❌ NO | N/A |

**Key insight**: Even "error" statuses get a row in `pii_extraction` (with null RAW_LLM_RESPONSE if error, or partial response if truncated).

---

## Writing to GCS: Implementation (output_store.py)

### GcsJsonOutput Class

```python
class GcsJsonOutput:
    def __init__(self, bucket, prefix, source, run_id):
        self.bucket_name = bucket
        self.prefix = prefix
        self.source = source
        self.run_id = run_id  # UUID hex for this run
        self._bucket = storage_client.bucket(bucket_name)
        self._seq = 0  # Sequence counter for part files
    
    def write(self, rows: List[dict], batch_id: int) -> str:
        """Upload rows as one NDJSON object."""
        path = self._next_path(batch_id)  # Increments _seq
        body = "".join(
            json.dumps(r, separators=(",", ":"), default=str) + "\n"
            for r in rows
        ).encode("utf-8")
        blob = self._bucket.blob(path)
        blob.upload_from_string(body, content_type="application/x-ndjson")
        self.rows_written += len(rows)
        self.bytes_written += len(body)
        return f"gs://{bucket}/{path}"
```

**Key properties:**
- **Thread-safe**: Sequence counter guarded by lock (supports multi-threaded writer)
- **Idempotent**: Retry simply re-PUTs same key (overwrites, no duplication)
- **Batch-based**: One file per `write()` call, not per row
- **Compact**: `separators=(",", ":")` minimizes file size

---

## Queue Mode (QUEUE_MODE=1)

As of 2026-08-06, the pipeline can use queue mode:

**Normal mode**: Each guid's status marked in BigQuery immediately (UPDATE statements = DML locks = slow)

**Queue mode**: 
1. Read pending guids once at startup → build work queue
2. Process batch with zero BigQuery writes
3. Write status to GCS ledger file instead of BigQuery
4. `load_gcs_to_bq.py` applies ledger in bulk

**Benefit**: No DML locks during extraction, massively parallel processing possible.

---

## Recommendations for Tool 5 (load_to_bigquery)

Based on this analysis, Tool 5 should:

### Option 1: Follow Mosaic Pattern (Recommended)
1. **Write extracted rows to GCS as NDJSON**
   - Path: `gs://bucket/{prefix}/source=agentic/dt=YYYY-MM-DD/run={run_id}/batch-{batch_id}.jsonl`
   - Format: Newline-delimited JSON, compact separators
   
2. **Load to BigQuery in separate step**
   - Load NDJSON to staging table
   - Per-guid deduplication (newest EXTRACTED_AT wins)
   - Insert only non-duplicate rows to target

3. **Why?**
   - ✅ No streaming buffer locks
   - ✅ No DML lock contention
   - ✅ Idempotent (retries don't duplicate)
   - ✅ Auditable (files in GCS, immutable)
   - ✅ Scales to massive parallelism (96 workers)

### Option 2: Direct BigQuery Write (Not Recommended)
- Simpler, but hits all the problems mosaic solved
- Streaming inserts lock for 90 minutes
- Each write = DML lock on target table
- Can cause queue backup and stalls

---

## Implementation Details for Tool 5

### Row Output Format

Each person-row extraction becomes one JSON object:

```json
{
  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
  "PERSON_FULL_NAME": "John Smith",
  "PERSON_FIRST_NAME": "John",
  "PERSON_LAST_NAME": "Smith",
  "PERSON_EMAIL": "john@company.com",
  "PERSON_ID": "10001",
  "PERSON_ID_TYPE": "EMPLOYEE_ID",
  "PERSON_PHONE_NUM": "+1-555-123-4567",
  "JOB_TITLE": "Senior Engineer",
  "EXTRACTED_AT": "2026-08-13T18:00:00Z",
  "prompt_version": "v1",
  "RAW_LLM_RESPONSE": "{...full Tool 3 code output...}",
  "ERROR_MESSAGE": null,
  "qc_status": null,
  ...
  (all 49 PII fields + audit columns)
}
```

### File Naming Convention

```
batch-<batch_id>-part-<sequence>.jsonl

Example:
batch-000001-part-00001.jsonl  (first extraction batch)
batch-000002-part-00001.jsonl  (second extraction batch)
batch-000002-part-00002.jsonl  (overflow from second batch)
```

### Deduplication Strategy

If guid appears in multiple EXTRACTED_AT timestamps:
- Keep rows with MAX(EXTRACTED_AT)
- Delete older versions from BigQuery
- Insert new winning row-set

### Status Table Integration

Tool 5 should mark extraction status separately:
- `complete` if success_rate ≥ 95%
- `error_truncated` if partial (70-95% + LLM says acceptable)
- `error_llm` if complete failure (< 70%)

---

## Summary Table

| Aspect | Implementation |
|--------|----------------|
| **Output Format** | NDJSON (newline-delimited JSON) |
| **Output Location** | GCS (not BigQuery directly) |
| **Partitioning** | Hive-style: source=/dt=/run= |
| **Deduplication** | Per-guid on EXTRACTED_AT (newest wins) |
| **Idempotency** | Retries overwrite same file (no duplicates) |
| **Loading** | Staging table + DELETE + INSERT (not MERGE) |
| **Audit Trail** | RAW_LLM_RESPONSE + ERROR_MESSAGE + EXTRACTED_AT |
| **Scalability** | Supports 96+ parallel workers |
| **Risk Profile** | No streaming buffer locks, no DML contention |

