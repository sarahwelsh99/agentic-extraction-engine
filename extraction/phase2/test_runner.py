"""Phase 2.2: Test Runner

Executes generated extractors on test samples to verify functionality
before deployment to Phase 4 production scale.

Tests:
1. Positive cases: Original sample data
2. Negative cases: Edge cases, malformed data
3. Schema compliance: Output structure matches mosaic schema

Uses the same schema as mosaic-glean-extraction project.
"""
import logging
import json
from typing import List, Dict, Any, Tuple
import config

logger = logging.getLogger(__name__)


def run_tests_on_samples(code: str,
                        samples: List[Dict[str, Any]],
                        required_fields: List[str] = None) -> Dict[str, Any]:
    """Run extractors on test samples and collect results.

    Args:
        code: Generated Python code
        samples: List of test samples {title, body_text, ...}
        required_fields: Fields that must be present in output

    Returns:
        Test results with pass/fail summary
    """
    required_fields = required_fields or []

    # Import the extractor
    namespace: Dict[str, Any] = {}
    try:
        exec(code, namespace)
    except Exception as e:
        return {
            "status": "failed",
            "error": f"Failed to load extractor: {e}",
            "tests_run": 0,
            "tests_passed": 0
        }

    extract_pii = namespace.get('extract_pii')
    if not extract_pii:
        return {
            "status": "failed",
            "error": "extract_pii function not found",
            "tests_run": 0,
            "tests_passed": 0
        }

    # Run tests
    results: List[Dict[str, Any]] = []
    passed = 0
    failed = 0

    for i, sample in enumerate(samples):
        try:
            title = sample.get('title', '')
            body_text = sample.get('body_text', '')

            result = extract_pii(title, body_text)

            # Validate result structure
            if not isinstance(result, dict):
                results.append({
                    "sample_id": i,
                    "passed": False,
                    "error": f"Expected dict, got {type(result)}"
                })
                failed += 1
                continue

            # Check for required fields
            missing_fields = [f for f in required_fields if f not in result]
            if missing_fields:
                results.append({
                    "sample_id": i,
                    "passed": False,
                    "error": f"Missing fields: {missing_fields}",
                    "returned": list(result.keys())
                })
                failed += 1
                continue

            results.append({
                "sample_id": i,
                "passed": True,
                "fields_extracted": {k: v for k, v in result.items() if v is not None}
            })
            passed += 1

        except Exception as e:
            results.append({
                "sample_id": i,
                "passed": False,
                "error": str(e)
            })
            failed += 1

    return {
        "status": "completed",
        "tests_run": len(samples),
        "tests_passed": passed,
        "tests_failed": failed,
        "pass_rate": passed / len(samples) if samples else 0.0,
        "results": results[:10]  # Return first 10 for brevity
    }


def generate_edge_case_tests() -> List[Dict[str, str]]:
    """Generate edge case test samples.

    Returns:
        List of edge case samples
    """
    return [
        {
            "title": "Empty Document",
            "body_text": ""
        },
        {
            "title": "",
            "body_text": "Just a body with no title"
        },
        {
            "title": "Only Title",
            "body_text": None
        },
        {
            "title": "Special Characters: !@#$%^&*()",
            "body_text": "Body with special chars: !@#$%^&*()"
        },
        {
            "title": "Very Long Title" * 100,
            "body_text": "Very long body " * 1000
        },
        {
            "title": "Numbers Only",
            "body_text": "1234567890" * 100
        },
        {
            "title": "Unicode Test",
            "body_text": "Unicode: 你好 مرحبا Привет"
        },
    ]
