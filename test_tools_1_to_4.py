#!/usr/bin/env python3
"""Test runner for Tools 1-4 on 50 real documents from glean.

Fetches 50 high-PII documents from glean.drive_files using the provided query,
runs Tools 1-4 on each, and generates comprehensive analysis report.
"""

import json
import logging
import sys
import time
from pathlib import Path
from google.cloud import bigquery

from tools import get_tool_by_name
from extraction.pipeline_analyzer import PipelineAnalyzer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def fetch_test_documents(limit: int = 50) -> list:
    """Fetch test documents from glean using the provided query.

    Args:
        limit: Number of documents to fetch

    Returns:
        List of dicts with: guid, title, doc_type, body_text, char_len, pii_score
    """
    logger.info(f"Fetching {limit} test documents from glean...")

    query = """
    WITH pop AS (
        SELECT
          guid, title, doc_type, body_text,
          LENGTH(body_text) AS char_len,
          SPLIT(body_text, '\\n')[SAFE_OFFSET(0)] AS header
        FROM glean.drive_files
        WHERE triage_category = 'INCL_STRUCTURED_RECORD'
          AND isDuplicate = false
    ),
    scored AS (
        SELECT
          guid, title, doc_type, char_len, body_text,
          (IF(REGEXP_CONTAINS(header, r'(?i)(name|nombre)'), 1, 0)
          + IF(REGEXP_CONTAINS(header, r'(?i)(birth|dob)'), 1, 0)
          + IF(REGEXP_CONTAINS(header, r'(?i)(ssn|national.*id|passport|cedula|curp)'), 1, 0)
          + IF(REGEXP_CONTAINS(header, r'(?i)(bank|account.*num|iban|swift)'), 1, 0)
          + IF(REGEXP_CONTAINS(header, r'(?i)(phone|mobile)'), 1, 0)
          + IF(REGEXP_CONTAINS(header, r'(?i)(email)'), 1, 0)
          + IF(REGEXP_CONTAINS(header, r'(?i)(salary|pay|wage|compensation)'), 1, 0)
          + IF(REGEXP_CONTAINS(header, r'(?i)(address|street|city|postal)'), 1, 0)
          + IF(REGEXP_CONTAINS(header, r'(?i)(employee.*id|emp.*id)'), 1, 0)
          ) AS pii_category_score
        FROM pop
    )
    SELECT guid, title, doc_type, char_len, pii_category_score, body_text
    FROM scored
    WHERE pii_category_score >= 5
    ORDER BY pii_category_score DESC, char_len DESC
    LIMIT {}
    """.format(
        limit
    )

    try:
        client = bigquery.Client()
        results = client.query(query).result()

        documents = []
        for row in results:
            documents.append(
                {
                    "guid": row.guid,
                    "title": row.title,
                    "doc_type": row.doc_type,
                    "char_len": row.char_len,
                    "pii_score": row.pii_category_score,
                    "body_text": row.body_text,
                }
            )

        logger.info(f"✓ Fetched {len(documents)} documents")
        return documents

    except Exception as e:
        logger.error(f"Failed to fetch documents: {e}")
        sys.exit(1)


def run_pipeline(
    guid: str, body_text: str
) -> tuple:
    """Run Tools 1-4 on a single document.

    Args:
        guid: Document GUID
        body_text: Document body text

    Returns:
        Tuple of (tool1_output, tool2_output, tool3_output, tool4_output)
    """
    try:
        # Tool 1: Fetch and sample
        tool1 = get_tool_by_name("fetch_and_sample")
        t1_start = time.time()
        tool1_response = tool1({"guid": guid, "body_text": body_text})
        tool1_output = json.loads(tool1_response)
        tool1_time = time.time() - t1_start

        if tool1_output.get("status") != "success":
            return tool1_output, None, None, None

        # Tool 2: Infer schema
        tool2 = get_tool_by_name("structural_inspector")
        t2_start = time.time()
        tool2_response = tool2(
            {
                "guid": guid,
                "raw_sample": tool1_output["raw_sample"],
                "detected_format_hint": tool1_output["detected_format_hint"],
                "actual_header_row_index": tool1_output.get("actual_header_row_index", 0),
            }
        )
        tool2_output = json.loads(tool2_response)
        tool2_time = time.time() - t2_start

        if tool2_output.get("status") != "success":
            return tool1_output, tool2_output, None, None

        # Tool 3: Generate code
        tool3 = get_tool_by_name("generate_parser_script")
        t3_start = time.time()
        tool3_response = tool3(
            {
                "guid": guid,
                "columns": tool2_output["columns"],
                "detected_schema": tool2_output["detected_schema"],
                "raw_sample": tool1_output["raw_sample"],
            }
        )
        tool3_output = json.loads(tool3_response)
        tool3_time = time.time() - t3_start

        if tool3_output.get("status") != "success":
            return tool1_output, tool2_output, tool3_output, None

        # Tool 4: Sandbox run
        tool4 = get_tool_by_name("sandbox_execute")
        t4_start = time.time()
        tool4_response = tool4(
            {
                "guid": guid,
                "generated_code": tool3_output["generated_code"]["code"],
                "body_text": body_text,
                "columns": tool2_output["columns"],
                "detected_schema": tool2_output["detected_schema"],
            }
        )
        tool4_output = json.loads(tool4_response)
        tool4_time = time.time() - t4_start

        # Add timing to outputs
        tool1_output["_timing"] = tool1_time
        tool2_output["_timing"] = tool2_time
        tool3_output["_timing"] = tool3_time
        tool4_output["_timing"] = tool4_time

        return tool1_output, tool2_output, tool3_output, tool4_output

    except Exception as e:
        logger.error(f"Error in pipeline for {guid}: {e}")
        return None, None, None, None


def main():
    """Run analysis on 50 test documents."""
    print("\n" + "=" * 80)
    print("TOOLS 1-4 COMPREHENSIVE ANALYSIS")
    print("=" * 80)

    # Fetch documents
    documents = fetch_test_documents(limit=50)

    if not documents:
        logger.error("No documents fetched")
        sys.exit(1)

    print(f"\n📊 Processing {len(documents)} documents...\n")

    # Initialize analyzer
    analyzer = PipelineAnalyzer(output_dir="analysis")

    # Store all outputs for reference
    all_outputs = []

    # Process each document
    success_count = 0
    failed_count = 0

    for i, doc in enumerate(documents, 1):
        guid = doc["guid"]
        title = doc["title"]
        doc_type = doc["doc_type"]
        body_text = doc["body_text"]

        print(f"  [{i:2d}/{len(documents)}] Processing {guid[:8]}... ", end="", flush=True)

        start = time.time()
        tool1_out, tool2_out, tool3_out, tool4_out = run_pipeline(guid, body_text)
        duration = time.time() - start

        if all([tool1_out, tool2_out, tool3_out, tool4_out]):
            print(f"✓ {duration:.2f}s")
            success_count += 1
        else:
            print(f"✗ {duration:.2f}s (failed)")
            failed_count += 1

        # Analyze document
        analyzer.analyze_document(
            guid=guid,
            title=title,
            doc_type=doc_type,
            body_text=body_text,
            tool1_output=tool1_out or {},
            tool2_output=tool2_out or {},
            tool3_output=tool3_out or {},
            tool4_output=tool4_out or {},
        )

        # Store outputs
        all_outputs.append(
            {
                "tool1": tool1_out,
                "tool2": tool2_out,
                "tool3": tool3_out,
                "tool4": tool4_out,
            }
        )

    print(
        f"\n✓ Processing complete: {success_count} passed, {failed_count} failed\n"
    )

    # Generate reports
    analyzer.save_full_outputs(documents, all_outputs)
    analyzer.generate_report()
    analyzer.generate_html_report()

    # Print failed documents summary
    failed_docs = analyzer.get_failed_documents()
    if failed_docs:
        print("\n⚠️  DOCUMENTS WITH ISSUES:")
        print("-" * 80)
        for guid, title, issues in failed_docs[:10]:  # Show first 10
            print(f"\n  GUID: {guid}")
            print(f"  Title: {title}")
            print("  Issues:")
            for issue in issues[:3]:  # Show first 3 issues
                print(f"    - {issue}")

    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)
    print("\n📁 Output files:")
    print(f"   - analysis/analysis_results.json  (complete results)")
    print(f"   - analysis/document_details.csv   (per-document summary)")
    print(f"   - analysis/full_outputs.jsonl     (full tool outputs)")
    print(f"   - analysis/analysis_report.html   (interactive report)")
    print("\n")


if __name__ == "__main__":
    main()
