# Tools 1-3: Input/Output Specification

Complete specification of inputs and outputs for the agentic extraction pipeline Tools 1, 2, and 3.

---

## TOOL 1: fetch_and_sample

**Purpose**: Fetch document from glean and extract raw sample data

### INPUT

```json
{
  "guid": "string (required)",
  "body_text": "string (optional)",
  "source_path": "string (optional)",
  "sample_size": "integer (optional, default: 5)"
}
```

**Parameters**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `guid` | string | YES | Document GUID to fetch from glean. Format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `body_text` | string | NO | Direct body text (if provided, skips glean fetch). Used for testing/direct input. |
| `source_path` | string | NO | GCS/file path (alternative to guid for local testing) |
| `sample_size` | integer | NO | Number of rows to sample from document (default: 5). Must be ≥ 2. |

**Example Input**:
```json
{
  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
  "body_text": "Employee ID,Full Name,Email\n10001,John Smith,john@company.com\n10002,Jane Doe,jane@company.com",
  "sample_size": 5
}
```

---

### OUTPUT

```json
{
  "status": "success|error",
  "source_path": "string|null",
  "source_type": "glean_document|local_file|gcs_path",
  "guid": "string",
  "raw_sample": "string",
  "detected_format_hint": "csv|json|tsv|pipe|other",
  "first_line_is_header": "boolean",
  "actual_header_row_index": "integer",
  "encoding": "utf-8",
  "total_bytes": "integer",
  "sample_size": "integer",
  "byte_sample_size": "integer",
  "error": "string|null",
  "timestamp": "ISO 8601 string"
}
```

**Response Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"success"` if data was fetched, `"error"` if failed |
| `source_path` | string\|null | Original source path (glean, GCS, or local file) |
| `source_type` | string | Where document came from |
| `guid` | string | Document GUID (echoed from input) |
| `raw_sample` | string | Raw CSV/JSON text sample (first N rows) |
| `detected_format_hint` | string | Detected format: `csv`, `json`, `tsv`, `pipe-delimited`, or `other` |
| `first_line_is_header` | boolean | Whether row 0 contains column headers |
| `actual_header_row_index` | integer | 0-based index of header row (usually 0) |
| `encoding` | string | Character encoding detected (usually `utf-8`) |
| `total_bytes` | integer | Total bytes in sample |
| `sample_size` | integer | Number of rows sampled |
| `byte_sample_size` | integer | Actual byte size of sample |
| `error` | string\|null | Error message if `status == "error"` |
| `timestamp` | string | ISO 8601 timestamp of fetch |

**Example Output**:
```json
{
  "status": "success",
  "source_path": null,
  "source_type": "glean_document",
  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
  "raw_sample": "Employee ID,Full Name,Email,Salary,Department\n10001,John Smith,john.smith@company.com,\"50,000\",Engineering\n10002,Jane Doe,jane.doe@company.com,\"65,000\",Sales\n10003,Bob Johnson,bob.johnson@company.com,\"45,000\",Operations",
  "detected_format_hint": "csv",
  "first_line_is_header": true,
  "actual_header_row_index": 0,
  "encoding": "utf-8",
  "total_bytes": 289,
  "sample_size": 3,
  "byte_sample_size": 289,
  "error": null,
  "timestamp": "2026-08-13T18:37:43.443835+00:00"
}
```

---

## TOOL 2: infer_schema_and_profile

**Purpose**: Analyze raw sample from Tool 1, infer column types, detect PII fields, identify data patterns

### INPUT

```json
{
  "guid": "string (required)",
  "raw_sample": "string (required)",
  "detected_format_hint": "string (required)",
  "actual_header_row_index": "integer (required)"
}
```

**Parameters**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `guid` | string | YES | Document GUID (from Tool 1 output). Used for tracking. |
| `raw_sample` | string | YES | Raw CSV/JSON text from Tool 1 `raw_sample` field |
| `detected_format_hint` | string | YES | Format from Tool 1 `detected_format_hint` field (e.g., `"csv"`) |
| `actual_header_row_index` | integer | YES | Header row index from Tool 1 (usually 0) |

**Example Input**:
```json
{
  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
  "raw_sample": "Employee ID,Full Name,Email,Salary,Department\n10001,John Smith,john.smith@company.com,\"50,000\",Engineering\n10002,Jane Doe,jane.doe@company.com,\"65,000\",Sales\n10003,Bob Johnson,bob.johnson@company.com,\"45,000\",Operations",
  "detected_format_hint": "csv",
  "actual_header_row_index": 0
}
```

---

### OUTPUT

```json
{
  "status": "success|error",
  "guid": "string",
  "columns": [
    {
      "name": "string",
      "detected_type": "integer|float|boolean|date|string",
      "nullable": "boolean",
      "pii_field": "string|null",
      "pii_confidence": "float (0.0-1.0)",
      "secondary_pii_fields": [
        {
          "field": "string",
          "inferred_value": "string",
          "confidence": "float"
        }
      ],
      "sample_values": ["string", "string"],
      "patterns": ["email|phone|ssn|currency|date|uuid", ...]
    }
  ],
  "detected_schema": {
    "format": "string",
    "delimiter": "string",
    "encoding": "string",
    "total_columns": "integer",
    "total_rows": "integer",
    "pii_columns": "integer"
  },
  "error": "string|null"
}
```

**Response Fields**:

| Field | Path | Type | Description |
|-------|------|------|-------------|
| `status` | `.` | string | `"success"` or `"error"` |
| `guid` | `.` | string | Document GUID (echoed from input) |
| `columns` | `.` | array | Array of column definitions (one per column) |
| `detected_schema` | `.` | object | Metadata about the detected schema |
| `error` | `.` | string\|null | Error message if `status == "error"` |

**Column Object** (items in `columns` array):

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Column name from header |
| `detected_type` | string | Inferred data type: `integer`, `float`, `boolean`, `date`, `string` |
| `nullable` | boolean | True if column has empty values |
| `pii_field` | string\|null | Mapped mosaic PII field (e.g., `"PERSON_ID"`, `"PERSON_EMAIL"`) or `null` |
| `pii_confidence` | float | Confidence score for PII mapping (0.0 = no confidence, 1.0 = certain) |
| `secondary_pii_fields` | array | Additional PII fields for this column (e.g., ID type fields) |
| `sample_values` | array | Sample values from this column (up to 3) |
| `patterns` | array | Detected patterns: `"email"`, `"phone"`, `"ssn"`, `"currency"`, `"date"`, `"uuid"` |

**Secondary PII Field Object** (items in `secondary_pii_fields`):

| Field | Type | Description |
|-------|------|-------------|
| `field` | string | Secondary PII field name (e.g., `"PERSON_ID_TYPE"`) |
| `inferred_value` | string | Inferred value (e.g., `"EMPLOYEE_ID"`, `"PATIENT_ID"`) |
| `confidence` | float | Confidence in this inference (0.0-1.0) |

**Schema Object** (`detected_schema`):

| Field | Type | Description |
|-------|------|-------------|
| `format` | string | Data format: `"csv"`, `"json"`, `"tsv"`, `"pipe"`, etc. |
| `delimiter` | string | Field delimiter (e.g., `","` for CSV, `"\t"` for TSV) |
| `encoding` | string | Character encoding (usually `"utf-8"`) |
| `total_columns` | integer | Number of columns in data |
| `total_rows` | integer | Number of data rows (excluding header) |
| `pii_columns` | integer | Count of columns with PII fields detected |

**Example Output**:
```json
{
  "status": "success",
  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
  "columns": [
    {
      "name": "Employee ID",
      "detected_type": "integer",
      "nullable": true,
      "pii_field": "PERSON_ID",
      "pii_confidence": 0.85,
      "secondary_pii_fields": [
        {
          "field": "PERSON_ID_TYPE",
          "inferred_value": "EMPLOYEE_ID",
          "confidence": 0.85
        }
      ],
      "sample_values": ["10001", "10002", "10003"],
      "patterns": []
    },
    {
      "name": "Full Name",
      "detected_type": "string",
      "nullable": true,
      "pii_field": "PERSON_FULL_NAME",
      "pii_confidence": 0.95,
      "secondary_pii_fields": [],
      "sample_values": ["John Smith", "Jane Doe", "Bob Johnson"],
      "patterns": []
    },
    {
      "name": "Email",
      "detected_type": "string",
      "nullable": true,
      "pii_field": "PERSON_EMAIL",
      "pii_confidence": 0.95,
      "secondary_pii_fields": [],
      "sample_values": ["john.smith@company.com", "jane.doe@company.com", "bob.johnson@company.com"],
      "patterns": ["email"]
    },
    {
      "name": "Salary",
      "detected_type": "string",
      "nullable": true,
      "pii_field": null,
      "pii_confidence": 0.0,
      "secondary_pii_fields": [],
      "sample_values": ["50,000", "65,000", "45,000"],
      "patterns": ["currency"]
    },
    {
      "name": "Department",
      "detected_type": "string",
      "nullable": true,
      "pii_field": null,
      "pii_confidence": 0.0,
      "secondary_pii_fields": [],
      "sample_values": ["Engineering", "Sales", "Operations"],
      "patterns": []
    }
  ],
  "detected_schema": {
    "format": "csv",
    "delimiter": ",",
    "encoding": "utf-8",
    "total_columns": 5,
    "total_rows": 3,
    "pii_columns": 3
  },
  "error": null
}
```

---

## TOOL 3: generate_parser_script

**Purpose**: Generate production-ready Python extraction code from schema inferred by Tool 2

### INPUT

```json
{
  "guid": "string (required)",
  "raw_sample": "string (required)",
  "columns": [
    {
      "name": "string",
      "detected_type": "string",
      "nullable": "boolean",
      "pii_field": "string|null",
      "pii_confidence": "float",
      "secondary_pii_fields": [],
      "sample_values": ["string"],
      "patterns": ["string"]
    }
  ],
  "detected_schema": {
    "format": "string",
    "delimiter": "string",
    "encoding": "string",
    "total_columns": "integer",
    "total_rows": "integer",
    "pii_columns": "integer"
  }
}
```

**Parameters**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `guid` | string | YES | Document GUID (from Tool 2 output). Used for tracking. |
| `raw_sample` | string | YES | Raw CSV/JSON text (from Tool 1). Included in prompt to vLLM. |
| `columns` | array | YES | Column definitions from Tool 2 output. Used to generate field mappings. |
| `detected_schema` | object | YES | Schema metadata from Tool 2 output. |

**Example Input**:
```json
{
  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
  "raw_sample": "Employee ID,Full Name,Email,Salary,Department\n10001,John Smith,john.smith@company.com,\"50,000\",Engineering\n10002,Jane Doe,jane.doe@company.com,\"65,000\",Sales\n10003,Bob Johnson,bob.johnson@company.com,\"45,000\",Operations",
  "columns": [
    {"name": "Employee ID", "detected_type": "integer", "nullable": true, "pii_field": "PERSON_ID", ...},
    {"name": "Full Name", "detected_type": "string", "nullable": true, "pii_field": "PERSON_FULL_NAME", ...},
    ...
  ],
  "detected_schema": {
    "format": "csv",
    "delimiter": ",",
    "encoding": "utf-8",
    "total_columns": 5,
    "total_rows": 3,
    "pii_columns": 3
  }
}
```

---

### OUTPUT

```json
{
  "status": "success|error",
  "guid": "string",
  "generated_code": {
    "language": "python",
    "code": "string (complete Python source code)",
    "format_spec": {
      "source_format": "string",
      "delimiter": "string",
      "encoding": "string",
      "has_header": "boolean",
      "header_row": "integer"
    },
    "syntax_valid": "boolean"
  },
  "field_mappings": {
    "column_name": "PII_FIELD|null",
    ...
  },
  "extraction_rules": {
    "required_fields": ["string"],
    "nullable_fields": ["string"],
    "field_types": {
      "column_name": "string"
    },
    "validation_rules": {
      "column_name": ["string"]
    }
  },
  "pii_extraction": {
    "pii_columns": "integer",
    "mappings": [
      {
        "source_column": "string",
        "target_pii_field": "string",
        "confidence": "float",
        "secondary_fields": [
          {
            "field": "string",
            "inferred_value": "string",
            "confidence": "float"
          }
        ]
      }
    ]
  },
  "code_quality": {
    "has_type_hints": "boolean",
    "has_error_handling": "boolean",
    "has_validation": "boolean",
    "has_documentation": "boolean",
    "has_row_tracking": "boolean",
    "generated_by": "vLLM"
  },
  "error": "string|null"
}
```

**Response Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"success"` or `"error"` |
| `guid` | string | Document GUID (echoed from input) |
| `generated_code` | object | Generated Python code and metadata |
| `field_mappings` | object | Source column → target PII field mappings |
| `extraction_rules` | object | Validation rules and field type info |
| `pii_extraction` | object | PII field mapping details with confidence |
| `code_quality` | object | Metadata about generated code quality |
| `error` | string\|null | Error message if `status == "error"` |

**Generated Code Object** (`generated_code`):

| Field | Type | Description |
|-------|------|-------------|
| `language` | string | Always `"python"` |
| `code` | string | Complete, runnable Python source code with imports, class definition, methods, docstrings |
| `format_spec.source_format` | string | Source data format (e.g., `"csv"`) |
| `format_spec.delimiter` | string | Field delimiter (e.g., `","`) |
| `format_spec.encoding` | string | Character encoding (e.g., `"utf-8"`) |
| `format_spec.has_header` | boolean | Whether first row is header (usually `true`) |
| `format_spec.header_row` | integer | Header row index (usually 0) |
| `syntax_valid` | boolean | Whether generated code has valid Python syntax |

**Field Mappings** (`field_mappings`):

Object where:
- **Key**: Source column name (string)
- **Value**: Target mosaic PII field name (string) or `null` if no PII mapping

Example:
```json
{
  "Employee ID": "PERSON_ID",
  "Full Name": "PERSON_FULL_NAME",
  "Email": "PERSON_EMAIL",
  "Salary": null,
  "Department": null
}
```

**Extraction Rules** (`extraction_rules`):

| Field | Type | Description |
|-------|------|-------------|
| `required_fields` | array | Column names that cannot be empty |
| `nullable_fields` | array | Column names that can be empty |
| `field_types` | object | Column name → detected type mapping |
| `validation_rules` | object | Column name → validation rules array |

Example:
```json
{
  "required_fields": ["Employee ID", "Full Name"],
  "nullable_fields": ["Salary", "Department"],
  "field_types": {
    "Employee ID": "integer",
    "Full Name": "string",
    "Email": "string (email)",
    "Salary": "string (currency)",
    "Department": "string"
  },
  "validation_rules": {
    "Employee ID": ["required", "must_be_integer"],
    "Full Name": ["required"],
    "Email": ["nullable", "must_match_email_pattern"],
    "Salary": ["nullable", "remove_currency_symbols"],
    "Department": ["nullable"]
  }
}
```

**PII Extraction** (`pii_extraction`):

| Field | Type | Description |
|-------|------|-------------|
| `pii_columns` | integer | Count of columns that have PII mappings |
| `mappings` | array | Array of PII field mapping objects |

PII Mapping Object:
```json
{
  "source_column": "Employee ID",
  "target_pii_field": "PERSON_ID",
  "confidence": 0.85,
  "secondary_fields": [
    {
      "field": "PERSON_ID_TYPE",
      "inferred_value": "EMPLOYEE_ID",
      "confidence": 0.85
    }
  ]
}
```

**Code Quality** (`code_quality`):

| Field | Type | Description |
|-------|------|-------------|
| `has_type_hints` | boolean | Whether generated code includes type hints from `typing` module |
| `has_error_handling` | boolean | Whether code includes `try/except` error handling |
| `has_validation` | boolean | Whether code includes field validation logic |
| `has_documentation` | boolean | Whether code includes docstrings |
| `has_row_tracking` | boolean | Whether code tracks row numbers for error reporting |
| `generated_by` | string | Always `"vLLM"` |

**Example Output**:
```json
{
  "status": "success",
  "guid": "ddffbdb6-5041-4d65-a744-5a0631a629aa",
  "generated_code": {
    "language": "python",
    "code": "import csv\nimport re\nfrom typing import Dict, Any, List, Optional\n\nclass DataExtractor:\n    \"\"\"Extract and map data fields.\"\"\"\n    \n    FIELD_MAPPINGS = {\n        \"Employee ID\": \"PERSON_ID\",\n        \"Full Name\": \"PERSON_FULL_NAME\",\n        \"Email\": \"PERSON_EMAIL\",\n        \"Salary\": None,\n        \"Department\": None\n    }\n    \n    @classmethod\n    def extract(cls, file_path: str) -> List[Dict[str, Any]]:\n        \"\"\"Extract all rows from CSV file.\"\"\"\n        results = []\n        with open(file_path, 'r', encoding='utf-8') as f:\n            reader = csv.DictReader(f)\n            for row_num, row in enumerate(reader, start=2):\n                try:\n                    parsed = cls.parse_row(row)\n                    parsed['_row_number'] = row_num\n                    results.append(parsed)\n                except Exception as e:\n                    results.append({\n                        '_row_number': row_num,\n                        '_valid': False,\n                        '_errors': [str(e)]\n                    })\n        return results\n    \n    @classmethod\n    def parse_row(cls, row: Dict[str, str]) -> Dict[str, Any]:\n        \"\"\"Parse and validate a single row.\"\"\"\n        result = {}\n        errors = []\n        \n        # Extract and validate each field...\n        \n        result['_valid'] = len(errors) == 0\n        if errors:\n            result['_errors'] = errors\n        \n        return result",
    "format_spec": {
      "source_format": "csv",
      "delimiter": ",",
      "encoding": "utf-8",
      "has_header": true,
      "header_row": 0
    },
    "syntax_valid": true
  },
  "field_mappings": {
    "Employee ID": "PERSON_ID",
    "Full Name": "PERSON_FULL_NAME",
    "Email": "PERSON_EMAIL",
    "Salary": null,
    "Department": null
  },
  "extraction_rules": {
    "required_fields": ["Employee ID", "Full Name"],
    "nullable_fields": ["Salary", "Department"],
    "field_types": {
      "Employee ID": "integer",
      "Full Name": "string",
      "Email": "string (email)",
      "Salary": "string (currency)",
      "Department": "string"
    },
    "validation_rules": {
      "Employee ID": ["required", "must_be_integer"],
      "Full Name": ["required"],
      "Email": ["nullable", "must_match_email_pattern"],
      "Salary": ["nullable", "remove_currency_symbols"],
      "Department": ["nullable"]
    }
  },
  "pii_extraction": {
    "pii_columns": 3,
    "mappings": [
      {
        "source_column": "Employee ID",
        "target_pii_field": "PERSON_ID",
        "confidence": 0.85,
        "secondary_fields": [
          {
            "field": "PERSON_ID_TYPE",
            "inferred_value": "EMPLOYEE_ID",
            "confidence": 0.85
          }
        ]
      },
      {
        "source_column": "Full Name",
        "target_pii_field": "PERSON_FULL_NAME",
        "confidence": 0.95,
        "secondary_fields": []
      },
      {
        "source_column": "Email",
        "target_pii_field": "PERSON_EMAIL",
        "confidence": 0.95,
        "secondary_fields": []
      }
    ]
  },
  "code_quality": {
    "has_type_hints": true,
    "has_error_handling": true,
    "has_validation": true,
    "has_documentation": true,
    "has_row_tracking": true,
    "generated_by": "vLLM"
  },
  "error": null
}
```

---

## Data Flow: Tool 1 → Tool 2 → Tool 3

```
┌─────────────────────────────────────────────────────────────┐
│ TOOL 1: fetch_and_sample                                    │
│                                                              │
│ INPUT: guid, body_text or source_path                       │
│ ↓                                                            │
│ OUTPUT: raw_sample (CSV text), detected_format_hint         │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ TOOL 2: infer_schema_and_profile                            │
│                                                              │
│ INPUT: raw_sample, detected_format_hint                     │
│        (from Tool 1 output)                                 │
│ ↓                                                            │
│ OUTPUT: columns array with types, PII fields, patterns      │
│         detected_schema with metadata                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ↓
┌─────────────────────────────────────────────────────────────┐
│ TOOL 3: generate_parser_script                              │
│                                                              │
│ INPUT: columns, detected_schema                             │
│        (from Tool 2 output)                                 │
│        + raw_sample (from Tool 1 output)                    │
│ ↓                                                            │
│ OUTPUT: Python extraction code                              │
│         field_mappings, extraction_rules, pii_extraction    │
└─────────────────────────────────────────────────────────────┘
```

---

## Mosaic PII Schema Fields (49 total)

Tool 2 and 3 map columns to these canonical fields:

```
RECORD_TYPE, JURISDICTION, TELUS_BUSINESS,
COMPANY_NAME, DOCUMENT_CLASSIFICATION, BOOL_PERSONAL_DATA,
PERSON_FULL_NAME, PERSON_FIRST_NAME, PERSON_MIDDLE_NAME, PERSON_LAST_NAME, PERSON_SUFFIX,
PERSON_ID_TYPE, PERSON_ID,
PERSON_EMAIL, PERSON_PHONE_NUM, PHONE_ID,
PERSON_DATE_OF_BIRTH,
PERSON_TAX_ID,
PERSON_ADDRESS_FULL, PERSON_ADDRESS_STREET, PERSON_ADDRESS_LINE2,
PERSON_ADDRESS_CITY, PERSON_ADDRESS_STATE, PERSON_ADDRESS_ZIP, PERSON_ADDRESS_COUNTRY,
FULL_CC_NUM, CC_CVV, CC_EXPIRATION,
DRIVERS_LICENSE, PASSPORT, MILITARY_ID, GOVERNMENT_ID,
BANK_ACCT_NUM, BANK_ROUTING_NUM,
GEOLOCATION,
PATIENT_ID_TYPE, PATIENT_ID,
IMEI_NUM, IMSI_NUM, E_SIM_SIM_EZ,
BOOL_EMPLOYEE_COMPENSATION, BOOL_BIOMETRIC_DATA, BOOL_DIGITAL_SIGNATURE,
BOOL_PERSONAL_CHARACTERISTICS, BOOL_END_USER_CONTRACT, BOOL_PATIENT_HISTORY,
PASSWORD_PIN,
JOB_TITLE,
OTHER_PII_TYPES
```

### Common Aliases (31 mappings)

Tool 2 uses these aliases for keyword matching:
- `ssn` → `PERSON_TAX_ID`
- `dob`, `date_of_birth`, `birth_date`, `birthdate` → `PERSON_DATE_OF_BIRTH`
- `email`, `email_address` → `PERSON_EMAIL`
- `phone`, `phone_number` → `PERSON_PHONE_NUM`
- `name`, `full_name` → `PERSON_FULL_NAME`
- `address`, `address_full` → `PERSON_ADDRESS_FULL`
- `job_title`, `title`, `role`, `position`, `designation` → `JOB_TITLE`
- And 24 more...

