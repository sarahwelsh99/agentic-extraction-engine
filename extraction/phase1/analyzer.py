"""Phase 1.1: Pattern Analysis

Analyzes sample payloads to identify structural patterns, field locations,
and data formatting conventions.

Uses local vLLM to understand:
- What fields are present in the data
- Where they typically appear (header, body, footer, etc.)
- Common value formats (email patterns, phone patterns, etc.)
- Edge cases and variations

Output: Pattern summary (JSON) and analysis report
"""
import logging
from typing import List, Dict, Any, Optional
import json
from llm_service import get_llm_client
import config

logger = logging.getLogger(__name__)


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

    # Build analysis prompt
    sample_text = "\n\n---\n\n".join([
        f"Sample {i}:\nTitle: {s.get('title', 'N/A')}\nBody:\n{s.get('body_text', '')[:1000]}"
        for i, s in enumerate(samples[:10])
    ])

    system_prompt = """You are an expert data analyst. Analyze the provided sample documents
and identify:
1. What information types are present (names, emails, addresses, phone numbers, etc.)
2. Where these typically appear (headers, tables, lists, signatures, etc.)
3. Common formatting patterns
4. Edge cases to handle
5. Structured vs. unstructured content

Provide your analysis as JSON."""

    user_prompt = f"""Analyze these sample documents for extraction patterns:

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
