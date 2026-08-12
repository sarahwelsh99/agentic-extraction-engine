# PII Extraction Schema

This pipeline uses the **exact same schema** as the [mosaic-glean-extraction](https://github.com/sarahwelsh99/mosaic-glean-extraction) project.

## Target Fields (45 Total)

### Document/Record Information
- `RECORD_TYPE` — Type of record (invoice, contract, memo, etc.)
- `JURISDICTION` — Geographic jurisdiction (country, province, state)
- `TELUS_BUSINESS` — TELUS business unit indicator
- `COMPANY_NAME` — Company or organization name
- `DOCUMENT_CLASSIFICATION` — Document classification (public, confidential, etc.)

### Personal Information
- `PERSON_FULL_NAME` — Complete name
- `PERSON_FIRST_NAME` — First name
- `PERSON_MIDDLE_NAME` — Middle name
- `PERSON_LAST_NAME` — Last name
- `PERSON_SUFFIX` — Name suffix (Jr., Sr., III, etc.)
- `PERSON_EMAIL` — Email address
- `PERSON_PHONE_NUM` — Phone number
- `PERSON_DATE_OF_BIRTH` — Date of birth (YYYY-MM-DD)

### Identification
- `PERSON_ID_TYPE` — Type of ID (employee, customer, etc.)
- `PERSON_ID` — ID value
- `PERSON_TAX_ID` — Tax ID / SSN / SIN
- `DRIVERS_LICENSE` — Driver's license number
- `PASSPORT` — Passport number
- `MILITARY_ID` — Military ID number
- `GOVERNMENT_ID` — Government-issued ID
- `PHONE_ID` — Phone device identifier (IMEI, etc.)

### Address Information
- `PERSON_ADDRESS_FULL` — Complete mailing address
- `PERSON_ADDRESS_STREET` — Street address
- `PERSON_ADDRESS_LINE2` — Apartment, suite, etc.
- `PERSON_ADDRESS_CITY` — City
- `PERSON_ADDRESS_STATE` — State / Province
- `PERSON_ADDRESS_ZIP` — Postal code
- `PERSON_ADDRESS_COUNTRY` — Country

### Financial Information
- `FULL_CC_NUM` — Credit card number (SENSITIVE)
- `CC_CVV` — Credit card security code (SENSITIVE)
- `CC_EXPIRATION` — Card expiration date
- `BANK_ACCT_NUM` — Bank account number (SENSITIVE)
- `BANK_ROUTING_NUM` — Bank routing number

### Employment & Compensation
- `JOB_TITLE` — Job title / position / role
- `BOOL_EMPLOYEE_COMPENSATION` — Whether document contains compensation data

### Healthcare
- `PATIENT_ID_TYPE` — Type of patient identifier
- `PATIENT_ID` — Patient ID / health record number
- `BOOL_PATIENT_HISTORY` — Whether document contains medical history

### Device & Communication
- `IMEI_NUM` — Device IMEI number
- `IMSI_NUM` — SIM card IMSI number
- `E_SIM_SIM_EZ` — eSIM identifier

### Credentials & Secrets
- `PASSWORD_PIN` — Password, PIN, passcode, token, security code (HIGHLY SENSITIVE)

### Sensitive Data Flags
- `BOOL_PERSONAL_DATA` — Whether contains personal data
- `BOOL_BIOMETRIC_DATA` — Whether contains biometric data
- `BOOL_DIGITAL_SIGNATURE` — Whether contains digital signatures
- `BOOL_PERSONAL_CHARACTERISTICS` — Whether describes personal characteristics
- `BOOL_END_USER_CONTRACT` — Whether is end-user contract
- `GEOLOCATION` — Geographic coordinates or location

### Other
- `OTHER_PII_TYPES` — Catch-all for PII not fitting other categories

## Field Aliases

The LLM sometimes uses natural-language field names instead of canonical ones. These are automatically remapped:

| LLM Output | Canonical Field |
|---|---|
| `ssn`, `social_security_number`, `tin`, `tax_id` | `PERSON_TAX_ID` |
| `dob`, `date_of_birth`, `birth_date`, `birthdate` | `PERSON_DATE_OF_BIRTH` |
| `email`, `email_address` | `PERSON_EMAIL` |
| `phone`, `phone_number` | `PERSON_PHONE_NUM` |
| `name`, `full_name` | `PERSON_FULL_NAME` |
| `address`, `address_full` | `PERSON_ADDRESS_FULL` |
| `province`, `person_address_province` | `PERSON_ADDRESS_STATE` |
| `country` | `PERSON_ADDRESS_COUNTRY` |
| `pin`, `password`, `passcode`, `temporary_passcode`, `token`, `security_code`, `rsa_pin` | `PASSWORD_PIN` |
| `job_title`, `title`, `role`, `position`, `designation` | `JOB_TITLE` |

## Schema Features

### Redundancy Prevention
- **Aliases**: Natural-language names are rerouted to prevent off-schema keys
- **Dedup**: `PERSON_FULL_NAME` is primary dedup key (same person = same record merged)
- **No Mirroring**: Prevents LLM from inventing keys like "OPERATIONS_MANAGER"

### Structured vs Unstructured
- **Boolean fields** (`BOOL_*`) indicate presence of sensitive data types
- **String fields** contain actual extracted values
- **Required fields**: None (all NULLABLE, documents vary in what they contain)

### Sensitivity Levels
- **CRITICAL**: `FULL_CC_NUM`, `CC_CVV`, `BANK_ACCT_NUM`, `PASSWORD_PIN`
- **SENSITIVE**: `PERSON_TAX_ID`, `DRIVERS_LICENSE`, `PASSPORT`, `MILITARY_ID`
- **NORMAL**: Names, emails, addresses, phone numbers
- **METADATA**: Document type, jurisdiction, classification

## How Phase 1 Uses This Schema

Phase 1 analyzes your 20 sample documents and tells the LLM:
- "Here's the target schema (45 fields)"
- "Analyze the samples to find where these fields appear"
- "Generate Python code to extract them deterministically"

The generated code will:
```python
def extract_pii(title: str, body_text: str) -> dict:
    return {
        "PERSON_EMAIL": extract_emails(text),
        "PERSON_PHONE_NUM": extract_phones(text),
        "PERSON_FULL_NAME": extract_names(text),
        "PERSON_DATE_OF_BIRTH": extract_dob(text),
        # ... other fields
        "PERSON_ADDRESS_FULL": None,  # Not found in data
        "FULL_CC_NUM": None,          # Not present
    }
```

## Compatibility with Mosaic

✅ **100% compatible** with mosaic-glean-extraction:
- Same field names
- Same alias mappings
- Same dedup logic
- Same output structure
- Can write to same BigQuery table

This means:
- You can compare extraction quality with mosaic baseline
- Switch between pipelines without schema migration
- Share findings and patterns
- Use mosaic's existing analysis and quality reports

## Configuration

The schema is defined in two places:

1. **extraction/config.py**
   ```python
   SCHEMA_FIELDS = [...]  # 45 field names
   SCHEMA_ALIASES = {...} # 37 alias mappings
   ```

2. **extraction/schemas/mosaic_pii_schema.json**
   ```json
   {
     "target_fields": [
       {"name": "PERSON_EMAIL", "type": "string", ...},
       ...
     ]
   }
   ```

Phase 1 loads the JSON file to provide context to the LLM.

## Related

- [mosaic-glean-extraction schema](https://github.com/sarahwelsh99/mosaic-glean-extraction/blob/main/extraction/config.py#L45-L145)
- [CLAUDE.md](CLAUDE.md) - Operating notes
- [README.md](README.md) - Quick start
