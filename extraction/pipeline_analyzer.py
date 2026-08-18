"""Comprehensive pipeline analyzer for Tools 1-4 validation and error detection.

Records GUID for each document and validates output from each tool,
identifying inaccuracies and generating detailed reports.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ToolValidator:
    """Validates output from each tool and records issues."""

    def __init__(self):
        self.issues = {}

    def validate_tool1(self, guid: str, output: Dict[str, Any]) -> List[str]:
        """Validate Tool 1 (fetch_and_sample) output.

        Checks:
        - Status is success
        - Format detected (csv/json)
        - Sample exists and has content
        - Header row identified
        """
        issues = []

        if output.get("status") != "success":
            issues.append(f"Tool 1 failed: {output.get('error')}")
            return issues

        if not output.get("detected_format_hint"):
            issues.append("Tool 1: Format not detected")

        raw_sample = output.get("raw_sample", "")
        if not raw_sample or len(raw_sample.strip()) < 10:
            issues.append(f"Tool 1: Sample too small ({len(raw_sample)} chars)")

        if not isinstance(output.get("sample_size"), int):
            issues.append("Tool 1: Sample size not recorded")

        if output.get("actual_header_row_index") is None:
            issues.append("Tool 1: Header row not identified")

        return issues

    def validate_tool2(self, guid: str, output: Dict[str, Any]) -> List[str]:
        """Validate Tool 2 (structural_inspector) output.

        Checks:
        - Status is success
        - Columns identified
        - Schema populated
        - Type inference reasonable
        - PII fields detected
        """
        issues = []

        if output.get("status") != "success":
            issues.append(f"Tool 2 failed: {output.get('error')}")
            return issues

        columns = output.get("columns", [])
        if not columns or len(columns) == 0:
            issues.append("Tool 2: No columns identified")
            return issues

        schema = output.get("detected_schema", {})
        if not schema or schema.get("total_columns", 0) == 0:
            issues.append("Tool 2: Schema not populated")

        # Check column definitions
        for col in columns:
            col_name = col.get("name")
            col_type = col.get("detected_type")

            if not col_name:
                issues.append("Tool 2: Column with no name")
            if not col_type or col_type not in [
                "string",
                "integer",
                "float",
                "boolean",
                "date",
            ]:
                issues.append(
                    f"Tool 2: Column '{col_name}' has invalid type: {col_type}"
                )

            # Check if type inference looks reasonable
            if col_type == "integer" and col.get("nullable") is None:
                issues.append(f"Tool 2: Column '{col_name}' nullable flag missing")

        # PII detection check
        pii_columns = schema.get("pii_columns", 0)
        if pii_columns < 0:
            issues.append(f"Tool 2: Negative PII column count ({pii_columns})")

        return issues

    def validate_tool3(self, guid: str, output: Dict[str, Any]) -> List[str]:
        """Validate Tool 3 (generate_parser_script) output.

        Checks:
        - Status is success
        - Code generated
        - Syntax is valid
        - Code has required structure
        """
        issues = []

        if output.get("status") != "success":
            issues.append(f"Tool 3 failed: {output.get('error')}")
            return issues

        generated_code = output.get("generated_code", {})
        code = generated_code.get("code", "")

        if not code or len(code) < 50:
            issues.append(f"Tool 3: Generated code too short ({len(code)} chars)")

        # Check syntax validity
        syntax_valid = generated_code.get("syntax_valid")
        if syntax_valid is False:
            issues.append("Tool 3: Generated code has syntax errors")

        # Check for required components
        if "class DataExtractor" not in code:
            issues.append("Tool 3: Missing DataExtractor class")

        if "def parse_row" not in code:
            issues.append("Tool 3: Missing parse_row method")

        if "_valid" not in code or "_errors" not in code:
            issues.append("Tool 3: Missing _valid or _errors fields in output")

        # Check quality metrics
        code_quality = output.get("code_quality", {})
        if not code_quality.get("has_type_hints"):
            issues.append("Tool 3: Code missing type hints")

        if not code_quality.get("has_error_handling"):
            issues.append("Tool 3: Code missing error handling")

        return issues

    def validate_tool4(self, guid: str, output: Dict[str, Any]) -> List[str]:
        """Validate Tool 4 (sandbox_run_and_evaluate) output.

        Checks:
        - Status is success or reasonable error
        - Rows extracted
        - Quality metrics present
        - Success rate acceptable
        """
        issues = []

        if output.get("status") != "success":
            issues.append(f"Tool 4 failed: {output.get('error')}")
            return issues

        # Check extraction results
        extraction_results = output.get("extraction_results", [])
        if not extraction_results or len(extraction_results) == 0:
            issues.append("Tool 4: No rows extracted")

        # Check quality metrics
        metrics = output.get("quality_metrics", {})
        success_rate = metrics.get("success_rate", 0)

        if success_rate < 0 or success_rate > 1:
            issues.append(f"Tool 4: Invalid success rate ({success_rate})")

        if success_rate < 0.7:
            issues.append(
                f"Tool 4: Low success rate ({success_rate:.1%}) - extraction may have issues"
            )

        # Check validation result
        validation = output.get("validation_result")
        if validation not in ["success", "failure", "review"]:
            issues.append(f"Tool 4: Invalid validation result ({validation})")

        return issues


class PipelineAnalyzer:
    """Comprehensive analyzer for Tools 1-4 pipeline."""

    def __init__(
        self, output_dir: str = "analysis", max_documents: int = 50
    ):
        """Initialize analyzer.

        Args:
            output_dir: Directory to store analysis results
            max_documents: Max documents to analyze
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.max_documents = max_documents
        self.validator = ToolValidator()

        self.results = {
            "metadata": {
                "start_time": None,
                "end_time": None,
                "total_documents": 0,
                "timestamp": datetime.now().isoformat(),
            },
            "summary": {
                "tool1": {"success": 0, "failed": 0, "error_count": 0},
                "tool2": {"success": 0, "failed": 0, "error_count": 0},
                "tool3": {"success": 0, "failed": 0, "error_count": 0},
                "tool4": {"success": 0, "failed": 0, "error_count": 0},
            },
            "documents": {},
            "issues_by_type": {},
        }

    def analyze_document(
        self,
        guid: str,
        title: str,
        doc_type: str,
        body_text: str,
        tool1_output: Dict[str, Any],
        tool2_output: Dict[str, Any],
        tool3_output: Dict[str, Any],
        tool4_output: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Analyze single document through all tools.

        Args:
            guid: Document GUID
            title: Document title
            doc_type: Document type
            body_text: Document body
            tool1_output: Output from Tool 1
            tool2_output: Output from Tool 2
            tool3_output: Output from Tool 3
            tool4_output: Output from Tool 4

        Returns:
            Analysis result for this document
        """
        result = {
            "guid": guid,
            "title": title,
            "doc_type": doc_type,
            "body_length": len(body_text),
            "tools": {
                "tool1": {
                    "status": tool1_output.get("status"),
                    "issues": self.validator.validate_tool1(guid, tool1_output),
                },
                "tool2": {
                    "status": tool2_output.get("status"),
                    "issues": self.validator.validate_tool2(guid, tool2_output),
                },
                "tool3": {
                    "status": tool3_output.get("status"),
                    "issues": self.validator.validate_tool3(guid, tool3_output),
                },
                "tool4": {
                    "status": tool4_output.get("status"),
                    "issues": self.validator.validate_tool4(guid, tool4_output),
                },
            },
            "overall_status": "success",
        }

        # Update summary
        for tool_name in ["tool1", "tool2", "tool3", "tool4"]:
            tool_result = result["tools"][tool_name]
            issues = tool_result["issues"]

            if tool_result["status"] == "success" and len(issues) == 0:
                self.results["summary"][tool_name]["success"] += 1
            else:
                self.results["summary"][tool_name]["failed"] += 1
                self.results["summary"][tool_name]["error_count"] += len(issues)
                result["overall_status"] = "failed"

            # Track issues by type
            for issue in issues:
                issue_type = issue.split(":")[0]
                if issue_type not in self.results["issues_by_type"]:
                    self.results["issues_by_type"][issue_type] = []
                self.results["issues_by_type"][issue_type].append(
                    {"guid": guid, "issue": issue}
                )

        # Store document result
        self.results["documents"][guid] = result

        return result

    def generate_report(self) -> None:
        """Generate comprehensive analysis report."""
        # Calculate summary statistics
        self.results["metadata"]["end_time"] = datetime.now().isoformat()

        total_docs = len(self.results["documents"])
        self.results["metadata"]["total_documents"] = total_docs

        # Success rates
        success_rates = {}
        for tool_name in ["tool1", "tool2", "tool3", "tool4"]:
            summary = self.results["summary"][tool_name]
            total = summary["success"] + summary["failed"]
            if total > 0:
                success_rates[tool_name] = (
                    summary["success"] / total * 100
                )
            else:
                success_rates[tool_name] = 0

        self.results["summary"]["success_rates"] = success_rates

        # Write results to JSON
        output_file = self.output_dir / "analysis_results.json"
        with open(output_file, "w") as f:
            json.dump(self.results, f, indent=2)

        logger.info(f"Analysis results written to {output_file}")

        # Print summary
        self._print_summary(success_rates)

        # Write per-document details
        self._write_document_details()

    def _print_summary(self, success_rates: Dict[str, float]) -> None:
        """Print analysis summary to console."""
        print("\n" + "=" * 80)
        print("PIPELINE ANALYSIS SUMMARY")
        print("=" * 80)
        print(f"\nTotal Documents: {self.results['metadata']['total_documents']}")

        print("\nTool Success Rates:")
        print("-" * 80)
        for tool_name in ["tool1", "tool2", "tool3", "tool4"]:
            summary = self.results["summary"][tool_name]
            success_rate = success_rates[tool_name]

            status = "✓" if success_rate >= 80 else "⚠" if success_rate >= 50 else "✗"
            print(
                f"  {status} {tool_name.upper()}: {success_rate:.1f}% "
                f"({summary['success']}/{summary['success'] + summary['failed']} docs)"
            )

        print("\nIssues by Tool:")
        print("-" * 80)
        for tool_name in ["tool1", "tool2", "tool3", "tool4"]:
            issue_count = self.results["summary"][tool_name]["error_count"]
            print(f"  {tool_name.upper()}: {issue_count} issues")

        print("\nMost Common Issues:")
        print("-" * 80)
        for issue_type, issues in sorted(
            self.results["issues_by_type"].items(),
            key=lambda x: len(x[1]),
            reverse=True,
        )[:10]:
            print(f"  - {issue_type}: {len(issues)} occurrences")

        print("\n" + "=" * 80)

    def _write_document_details(self) -> None:
        """Write per-document details to CSV for easy analysis."""
        import csv

        details_file = self.output_dir / "document_details.csv"
        with open(details_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "GUID",
                    "Title",
                    "Doc Type",
                    "Body Length",
                    "Overall Status",
                    "Tool1 Status",
                    "Tool1 Issues",
                    "Tool2 Status",
                    "Tool2 Issues",
                    "Tool3 Status",
                    "Tool3 Issues",
                    "Tool4 Status",
                    "Tool4 Issues",
                ]
            )

            for guid, doc_result in self.results["documents"].items():
                tool_results = doc_result["tools"]
                writer.writerow(
                    [
                        guid,
                        doc_result["title"],
                        doc_result["doc_type"],
                        doc_result["body_length"],
                        doc_result["overall_status"],
                        tool_results["tool1"]["status"],
                        "; ".join(tool_results["tool1"]["issues"][:2]),  # First 2 issues
                        tool_results["tool2"]["status"],
                        "; ".join(tool_results["tool2"]["issues"][:2]),
                        tool_results["tool3"]["status"],
                        "; ".join(tool_results["tool3"]["issues"][:2]),
                        tool_results["tool4"]["status"],
                        "; ".join(tool_results["tool4"]["issues"][:2]),
                    ]
                )

        logger.info(f"Document details written to {details_file}")

    def get_failed_documents(self) -> List[Tuple[str, str, List[str]]]:
        """Get documents with failures.

        Returns:
            List of (GUID, title, list of issues)
        """
        failed = []
        for guid, doc_result in self.results["documents"].items():
            if doc_result["overall_status"] == "failed":
                all_issues = []
                for tool_name in ["tool1", "tool2", "tool3", "tool4"]:
                    all_issues.extend(doc_result["tools"][tool_name]["issues"])

                failed.append((guid, doc_result["title"], all_issues))

        return failed

    def save_full_outputs(
        self, documents: List[Dict[str, Any]], outputs: List[Dict[str, Any]]
    ) -> None:
        """Save full tool outputs for each document.

        Args:
            documents: List of document metadata dicts with guid, title, etc.
            outputs: List of output dicts with tool1, tool2, tool3, tool4
        """
        output_file = self.output_dir / "full_outputs.jsonl"
        with open(output_file, "w") as f:
            for doc, output in zip(documents, outputs):
                entry = {
                    "guid": doc["guid"],
                    "title": doc.get("title"),
                    "doc_type": doc.get("doc_type"),
                    "outputs": output,
                }
                f.write(json.dumps(entry) + "\n")

        logger.info(f"Full outputs written to {output_file}")

    def generate_html_report(self) -> None:
        """Generate interactive HTML report."""
        failed_docs = self.get_failed_documents()
        total_docs = len(self.results["documents"])
        success_docs = total_docs - len(failed_docs)

        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Pipeline Analysis Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        .header {{ background: #2c3e50; color: white; padding: 20px; border-radius: 5px; }}
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }}
        .stat-box {{ background: white; padding: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .stat-number {{ font-size: 28px; font-weight: bold; color: #3498db; }}
        .stat-label {{ color: #7f8c8d; margin-top: 5px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; margin: 20px 0; }}
        th {{ background: #34495e; color: white; padding: 10px; text-align: left; }}
        td {{ padding: 10px; border-bottom: 1px solid #ecf0f1; }}
        tr:hover {{ background: #f9f9f9; }}
        .status-success {{ color: #27ae60; font-weight: bold; }}
        .status-failed {{ color: #e74c3c; font-weight: bold; }}
        .issues-list {{ list-style: none; padding: 0; }}
        .issues-list li {{ padding: 5px 0; }}
        .issue-item {{ background: #fff3cd; padding: 8px; border-left: 3px solid #ffc107; margin: 5px 0; }}
        .tool-summary {{ margin: 20px 0; }}
        .tool-name {{ font-size: 16px; font-weight: bold; color: #2c3e50; margin-top: 15px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Pipeline Analysis Report</h1>
        <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>

    <div class="summary">
        <div class="stat-box">
            <div class="stat-number">{total_docs}</div>
            <div class="stat-label">Total Documents</div>
        </div>
        <div class="stat-box">
            <div class="stat-number" style="color: #27ae60;">{success_docs}</div>
            <div class="stat-label">Successful</div>
        </div>
        <div class="stat-box">
            <div class="stat-number" style="color: #e74c3c;">{len(failed_docs)}</div>
            <div class="stat-label">Failed</div>
        </div>
        <div class="stat-box">
            <div class="stat-number">{success_docs/total_docs*100:.1f}%</div>
            <div class="stat-label">Success Rate</div>
        </div>
    </div>

    <div class="tool-summary">
        <h2>Tool Success Rates</h2>
        <table>
            <tr>
                <th>Tool</th>
                <th>Success Rate</th>
                <th>Successful</th>
                <th>Failed</th>
                <th>Total Issues</th>
            </tr>
"""

        for tool_name in ["tool1", "tool2", "tool3", "tool4"]:
            summary = self.results["summary"][tool_name]
            total = summary["success"] + summary["failed"]
            if total > 0:
                success_rate = summary["success"] / total * 100
            else:
                success_rate = 0

            status_class = "status-success" if success_rate >= 80 else "status-failed"
            html_content += f"""            <tr>
                <td><strong>{tool_name.upper()}</strong></td>
                <td class="{status_class}">{success_rate:.1f}%</td>
                <td>{summary['success']}</td>
                <td>{summary['failed']}</td>
                <td>{summary['error_count']}</td>
            </tr>
"""

        html_content += """        </table>
    </div>

    <div class="tool-summary">
        <h2>Failed Documents ({} total)</h2>
        <table>
            <tr>
                <th>GUID</th>
                <th>Title</th>
                <th>Issues</th>
            </tr>
""".format(
            len(failed_docs)
        )

        for guid, title, issues in failed_docs[:20]:  # Show first 20
            issues_html = "".join(
                [f'<div class="issue-item">{issue}</div>' for issue in issues[:3]]
            )
            html_content += f"""            <tr>
                <td><code>{guid[:8]}...</code></td>
                <td>{title}</td>
                <td>{issues_html}</td>
            </tr>
"""

        html_content += """        </table>
    </div>

</body>
</html>
"""

        html_file = self.output_dir / "analysis_report.html"
        with open(html_file, "w") as f:
            f.write(html_content)

        logger.info(f"HTML report written to {html_file}")
