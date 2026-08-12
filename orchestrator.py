"""Main orchestrator for agentic extraction pipeline.

Coordinates the four phases:
1. Phase 1: Pattern analysis & code generation
2. Phase 2: Safety validation & testing
3. Phase 3: Quality feedback loop
4. Phase 4: Deterministic execution at scale

Usage:
    python orchestrator.py --phase 1          # Run Phase 1 only
    python orchestrator.py --phase 1-4        # Run all phases
    python orchestrator.py --phase 4 --resume # Resume Phase 4 from checkpoint
"""
import logging
import argparse
import sys
import os

# Add extraction module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'extraction'))

import config
from gpu_monitor import create_monitor

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
gpu_monitor = create_monitor()


def run_phase_1():
    """Phase 1: Pattern Analysis & Code Generation"""
    logger.info("=== PHASE 1: Pattern Analysis & Code Generation ===")
    logger.info("Starting GPU monitoring...")
    gpu_monitor.print_status()

    from phase1.analyzer import extract_samples_from_bigquery, analyze_samples
    from phase1.code_generator import generate_extractors, save_extractor

    try:
        # Extract sample payloads
        logger.info(f"Fetching {config.PHASE1_SAMPLES_PER_SOURCE} samples from BigQuery...")
        samples = extract_samples_from_bigquery(limit=config.PHASE1_SAMPLES_PER_SOURCE)

        if not samples:
            logger.error("No samples found. Check BigQuery query and triage_category.")
            return False

        logger.info(f"Analyzing {len(samples)} samples for patterns...")
        analysis = analyze_samples(samples)

        if analysis.get("status") != "success":
            logger.error(f"Analysis failed: {analysis}")
            return False

        logger.info("Generating extraction code from patterns...")
        extractor_data = generate_extractors(analysis)

        if extractor_data.get("status") != "success":
            logger.error(f"Code generation failed: {extractor_data}")
            return False

        # Save extractor
        code_path = save_extractor(extractor_data, version="1.0", output_dir="extraction/generated")
        logger.info(f"Extractor saved to {code_path}")

        return True

    except Exception as e:
        logger.error(f"Phase 1 failed: {e}", exc_info=True)
        return False


def run_phase_2():
    """Phase 2: Safety Validation & Testing"""
    logger.info("=== PHASE 2: Safety Validation & Testing ===")

    try:
        from phase2.code_validator import validate_code_safety, test_import_extractors
        from phase2.test_runner import run_tests_on_samples, generate_edge_case_tests
        from phase1.analyzer import extract_samples_from_bigquery

        # Load generated code
        code_path = "extraction/generated/extractors_v1.0.py"
        if not os.path.exists(code_path):
            logger.error(f"Generated code not found at {code_path}. Run Phase 1 first.")
            return False

        with open(code_path) as f:
            code = f.read()

        # Validate safety
        logger.info("Validating code safety...")
        safety_result = validate_code_safety(code)
        logger.info(f"Safety validation: {safety_result['status']}")

        if safety_result["status"] == "REJECTED":
            logger.error(f"Safety violations: {safety_result['violations']}")
            return False

        # Test import
        logger.info("Testing code import...")
        import_result = test_import_extractors(code)
        if import_result["status"] != "success":
            logger.error(f"Import failed: {import_result['error']}")
            return False

        # Run tests on samples
        logger.info("Running tests on samples...")
        samples = extract_samples_from_bigquery(limit=config.PHASE2_TEST_SAMPLES)
        test_result = run_tests_on_samples(code, samples, required_fields=config.SCHEMA_FIELDS)
        logger.info(f"Test results: {test_result['tests_passed']}/{test_result['tests_run']} passed")

        # Test edge cases
        logger.info("Testing edge cases...")
        edge_cases = generate_edge_case_tests()
        edge_result = run_tests_on_samples(code, edge_cases)
        logger.info(f"Edge case results: {edge_result['tests_passed']}/{edge_result['tests_run']} passed")

        if test_result["pass_rate"] < 0.7:
            logger.warning(f"Test pass rate {test_result['pass_rate']} below 0.7 threshold")
            return False

        logger.info("Phase 2 validation PASSED")
        return True

    except Exception as e:
        logger.error(f"Phase 2 failed: {e}", exc_info=True)
        return False


def run_phase_3():
    """Phase 3: Quality Feedback Loop"""
    logger.info("=== PHASE 3: Quality Feedback Loop ===")
    logger.warning("Phase 3 not yet implemented. Skipping.")
    return True


def run_phase_4():
    """Phase 4: Deterministic Execution at Scale"""
    logger.info("=== PHASE 4: Deterministic Execution at Scale ===")
    logger.warning("Phase 4 not yet implemented. Skipping.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Agentic extraction pipeline orchestrator")
    parser.add_argument("--phase", default="1-4",
                       help="Phase(s) to run: 1, 2, 3, 4, or range like 1-4 (default: 1-4)")
    parser.add_argument("--resume", action="store_true",
                       help="Resume from checkpoint (Phase 4 only)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Validate configuration without running")

    args = parser.parse_args()

    # Validate config
    try:
        config.validate_config()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1

    if args.dry_run:
        logger.info("Dry-run mode: configuration is valid")
        return 0

    # Parse phase specification
    phases = []
    if "-" in args.phase:
        start, end = args.phase.split("-")
        phases = list(range(int(start), int(end) + 1))
    else:
        phases = [int(p.strip()) for p in args.phase.split(",")]

    # Run phases
    phase_functions = {
        1: run_phase_1,
        2: run_phase_2,
        3: run_phase_3,
        4: run_phase_4,
    }

    for phase_num in phases:
        if phase_num not in phase_functions:
            logger.error(f"Unknown phase: {phase_num}")
            return 1

        success = phase_functions[phase_num]()
        if not success:
            logger.error(f"Phase {phase_num} failed")
            return 1

    logger.info("Pipeline completed successfully")

    # Print GPU monitoring summary
    logger.info("\n=== GPU Utilization Summary ===")
    gpu_monitor.print_status()
    summary = gpu_monitor.get_summary()
    if "error" not in summary:
        logger.info(f"Average GPU Utilization: {summary['average_gpu_utilization_percent']:.1f}%")
        logger.info(f"All GPUs active: {summary['percent_time_all_gpus_active']:.1f}% of time")

    return 0


if __name__ == "__main__":
    sys.exit(main())
