"""Phase 1.1: Pattern Analysis

Analyzes sample payloads to identify structural patterns, field locations,
and data formatting conventions.

Uses local vLLM to understand:
- What fields are present in the data (from mosaic schema)
- Where they typically appear (header, body, footer, etc.)
- Common value formats (email patterns, phone patterns, etc.)
- Edge cases and variations

Output: Pattern summary (JSON) and analysis report

NOTE: Uses the exact same schema as mosaic-glean-extraction project.
"""
import logging
from typing import List, Dict, Any, Optional
import json
import os
from llm_service import get_llm_client
import config

logger = logging.getLogger(__name__)


def load_schema() -> Dict[str, Any]:
    """Load the PII extraction schema from JSON file.

    Returns:
        Schema dict with target_fields and other metadata
    """
    schema_file = config.SCHEMA_FILE
    if not os.path.exists(schema_file):
        logger.warning(f"Schema file not found: {schema_file}. Using config.SCHEMA_FIELDS instead.")
        return {
            "target_fields": [{"name": f} for f in config.SCHEMA_FIELDS],
            "source": "config.py"
        }

    try:
        with open(schema_file) as f:
            schema = json.load(f)
        logger.info(f"Loaded schema from {schema_file} ({len(schema.get('target_fields', []))} fields)")
        return schema
    except Exception as e:
        logger.error(f"Failed to load schema: {e}")
        raise


def analyze_samples(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze a batch of sample payloads for patterns.

    Args:
        samples: List of dicts with at least 'title' and 'body_text'

    Returns:
        Analysis result with identified patterns
    """
    if not samples:
        raise ValueError("Need at least one sample to analyze")

    llm = get_llm_client()
    schema = load_schema()
    schema_fields = [f["name"] for f in schema.get("target_fields", [])]

    # Build analysis prompt
    sample_text = "\n\n---\n\n".join([
        f"Sample {i}:\nTitle: {s.get('title', 'N/A')}\nBody:\n{s.get('body_text', '')[:1000]}"
        for i, s in enumerate(samples[:10])
    ])

    system_prompt = """You are an expert data analyst specializing in PII extraction.
Analyze the provided sample documents and identify:
1. Which target fields are present in the data
2. Where these typically appear (headers, tables, lists, signatures, etc.)
3. Common formatting patterns for each field
4. Edge cases to handle
5. Structured vs. unstructured content

Target fields to look for:
- Personal Info: PERSON_EMAIL, PERSON_PHONE_NUM, PERSON_FULL_NAME, PERSON_DATE_OF_BIRTH
- Address: PERSON_ADDRESS_FULL, PERSON_ADDRESS_CITY, PERSON_ADDRESS_STATE, etc.
- Identification: PERSON_TAX_ID, DRIVERS_LICENSE, PASSPORT, GOVERNMENT_ID
- Financial: FULL_CC_NUM, CC_CVV, BANK_ACCT_NUM, BANK_ROUTING_NUM
- Employment: JOB_TITLE, BOOL_EMPLOYEE_COMPENSATION
- Document Info: RECORD_TYPE, DOCUMENT_CLASSIFICATION, JURISDICTION

Provide your analysis as JSON."""

    user_prompt = f"""Analyze these sample documents for extraction patterns.
Target fields: {', '.join(schema_fields[:15])} ... and {len(schema_fields)-15} more

Sample documents:
{sample_text}

Provide analysis as JSON with keys: fields_present, patterns, formatting_conventions,
edge_cases, document_structure."""

    try:
        response = llm.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.0,
            max_tokens=2000
        )

        # Parse JSON response
        try:
            analysis = json.loads(response)
        except json.JSONDecodeError:
            # If response isn't valid JSON, return it as text in structured format
            analysis = {
                "raw_analysis": response,
                "fields_present": [],
                "patterns": [],
                "formatting_conventions": [],
                "edge_cases": []
            }

        return {
            "status": "success",
            "samples_analyzed": len(samples),
            "analysis": analysis
        }
    except Exception as e:
        logger.error(f"Pattern analysis failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "samples_analyzed": len(samples)
        }


def extract_samples_from_bigquery(limit: int = config.PHASE1_SAMPLES_PER_SOURCE) -> List[Dict]:
    """Fetch sample payloads from BigQuery for analysis.

    Args:
        limit: Number of samples to fetch

    Returns:
        List of sample documents with title and body_text
    """
    from bigquery_service import get_bigquery_client
    from google.cloud import bigquery

    client = get_bigquery_client()

    # Query source data (drive_files) matching triage category
    query = f"""
    SELECT
        id as guid,
        title,
        body_text,
        LENGTH(body_text) as body_length
    FROM `{config.SOURCE_PROJECT}.{config.SOURCE_TABLE}`
    WHERE triage_category = '{config.SOURCE_TRIAGE_CATEGORY}'
        AND body_text IS NOT NULL
        AND LENGTH(body_text) > 100
    ORDER BY RAND()
    LIMIT {limit}
    """

    try:
        results = client.query(query).result()
        samples = [
            {
                "guid": row.guid,
                "title": row.title,
                "body_text": row.body_text,
                "body_length": row.body_length
            }
            for row in results
        ]
        logger.info(f"Extracted {len(samples)} samples from BigQuery")
        return samples
    except Exception as e:
        logger.error(f"Failed to fetch samples: {e}")
        raise
