"""Tool 4: execute a generated script in a sandbox.

Runs the script from Tool 3 against the full document inside a Docker container
that has no network, a read-only filesystem, bounded memory and CPU, and an
unprivileged user — the code is model-written and unreviewed.

Reports what came out and nothing more. Whether the extraction worked is Tool 5's
question, so the criteria live in one place and this stays a thin executor.

Input: generated code from Tool 3, the document, the metadata report from Tool 2
Output: extracted rows and execution counts
"""

import json
import shutil
import subprocess
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional



class SandboxExecuteTool:
    """Execute generated extraction code in a Docker sandbox."""

    name = "sandbox_execute"
    description = "Execute a generated parser in a hardened Docker sandbox"

    DOCKER_IMAGE = "pii-extractor:latest"
    FAST_PATH_TIMEOUT = 60

    # Bounds on the untrusted code's container
    MEMORY_LIMIT = "512m"
    CPU_LIMIT = "1.0"
    PIDS_LIMIT = 64

    def __init__(self):
        self._ensure_docker_image()

    def __call__(self, inputs: Dict[str, Any]) -> str:
        """Execute the generated script against the document.

        Runs and reports, nothing more. Whether the result is good enough is
        Tool 5's decision, so that the criteria live in one place and this tool
        stays a thin, sandboxed executor.

        Args:
            inputs: {
                "guid": "document-guid",
                "generated_code": "Python code from Tool 3",
                "body_text": "the full document",
                "metadata_report": {...}  # From Tool 2: document structure
            }

        Returns:
            JSON string with the extracted rows and execution counts
        """
        try:
            guid = inputs.get("guid", "unknown")
            generated_code = inputs.get("generated_code", "")
            body_text = inputs.get("body_text", "")
            metadata_report = inputs.get("metadata_report") or {}

            if not generated_code:
                return json.dumps({
                    "status": "error",
                    "error": "Missing generated_code",
                })

            if not body_text:
                return json.dumps({
                    "status": "error",
                    "error": "Missing body_text",
                })

            result = self._run_extraction(generated_code, body_text, metadata_report)

            if result.get("status") == "error":
                return json.dumps({
                    "status": "error",
                    "guid": guid,
                    "error": result.get("error"),
                })

            return json.dumps({
                "status": "success",
                "guid": guid,
                "extracted_rows": result.get("extracted_rows", []),
                "total_rows": result.get("total_rows", 0),
                "total_records": result.get("total_records", 0),
                "skipped_wrong_shape": result.get("skipped_wrong_shape", 0),
            }, indent=2)

        except Exception as e:
            return json.dumps({
                "status": "error",
                "error": str(e),
            })

    def _ensure_docker_image(self) -> None:
        """Build the image if it is missing or older than what goes into it.

        Rebuilding on absence alone is not enough. The image carries copies of
        run_extraction.py and the shared splitter, so editing either leaves a
        running image that does not contain the edit — the code on disk and the
        code being executed drift apart silently, and the tests still pass
        because they exercise the disk copy. Comparing the image's build time
        against its sources closes that gap; Docker's layer cache makes the
        rebuild cheap when nothing of substance changed.
        """
        try:
            build_dir = os.path.dirname(__file__)
            dockerfile_path = os.path.join(build_dir, "Dockerfile")
            shared_records = os.path.join(
                os.path.dirname(os.path.dirname(build_dir)),
                "extraction", "core", "records.py",
            )

            # Refresh the shared splitter into the build context unconditionally.
            # Docker cannot COPY from outside the context, and a stale copy here
            # means the sandbox splits rows differently from the profiling tools.
            shutil.copyfile(shared_records, os.path.join(build_dir, "records.py"))

            if not self._image_is_stale(build_dir, dockerfile_path):
                return

            subprocess.run(
                ["docker", "build", "-t", self.DOCKER_IMAGE, "-f", dockerfile_path, "."],
                cwd=build_dir,
                capture_output=True,
                timeout=300,
            )
        except Exception as e:
            print(f"Warning: Could not ensure Docker image: {e}")

    def _image_is_stale(self, build_dir: str, dockerfile_path: str) -> bool:
        """Whether the image is missing, or predates the files it contains."""
        created = subprocess.run(
            ["docker", "image", "inspect", "-f", "{{.Created}}", self.DOCKER_IMAGE],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if created.returncode != 0 or not created.stdout.strip():
            return True

        try:
            built_at = datetime.fromisoformat(
                created.stdout.strip().replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            # Unreadable timestamp: rebuild rather than assume it is current
            return True

        sources = [
            dockerfile_path,
            os.path.join(build_dir, "run_extraction.py"),
            os.path.join(build_dir, "records.py"),
        ]
        return any(
            os.path.getmtime(path) > built_at
            for path in sources
            if os.path.exists(path)
        )

    def _run_extraction(
        self,
        generated_code: str,
        body_text: str,
        metadata_report: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Run extraction code in Docker container.

        The layout goes in with the document: generated code addresses cells by
        index, and those indices only mean anything against the same row
        splitting and the same table block that the schema was profiled from.

        Args:
            generated_code: Python code to execute
            body_text: Full document text
            metadata_report: Structure from Tool 2 (delimiter, header position)

        Returns:
            Dict with extraction results or error
        """
        report = metadata_report or {}

        job = json.dumps({
            "body_text": body_text,
            "delimiter": report.get("delimiter", ","),
            # Detected by Tool 2; the default only applies when the document
            # quotes nothing, in which case it cannot matter.
            "quote_char": report.get("quote_char") or '"',
            "header_row_index": report.get("header_row_index", 0),
            # Rows narrower than the header are normal (trailing empties are
            # trimmed); only rows too short to be this table are dropped.
            "min_field_count": max(1, int(report.get("modal_field_count", 1) * 0.5)),
            # Naming happens here, not in the generated code
            "column_names": report.get("header_names") or [],
            # And so does the width: the parser reads FIELD_COUNT rather than
            # embedding a number, which is what lets one parser be cached and
            # reused across documents of different widths.
            "field_count": report.get("modal_field_count") or 0,
        })

        try:
            # Write code to temp file to avoid env var size limits
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(generated_code)
                code_file = f.name
            # tempfile creates 0600 owned by this user; the container runs as an
            # unprivileged user of its own and must still be able to read it.
            os.chmod(code_file, 0o644)

            try:
                # Run Docker container with code mounted as volume.
                # The container exists to contain model-written code, so it gets
                # no network and bounded memory, CPU and process count.
                process = subprocess.run(
                    [
                        "docker", "run",
                        "--rm",
                        "--network", "none",
                        "--memory", self.MEMORY_LIMIT,
                        "--cpus", self.CPU_LIMIT,
                        "--pids-limit", str(self.PIDS_LIMIT),
                        "--read-only",
                        "--tmpfs", "/tmp:size=16m",
                        "-v", f"{code_file}:/app/generated_code.py:ro",
                        "-i",  # Read from stdin
                        self.DOCKER_IMAGE,
                    ],
                    input=job,
                    capture_output=True,
                    timeout=self.FAST_PATH_TIMEOUT,
                    text=True,
                )

                if process.returncode != 0:
                    return {
                        "status": "error",
                        "error": f"Docker execution failed (rc={process.returncode}): stderr={process.stderr} stdout={process.stdout[:500]}",
                    }

                # Parse JSON output from container
                try:
                    result = json.loads(process.stdout)
                    return result
                except json.JSONDecodeError as e:
                    return {
                        "status": "error",
                        "error": f"Invalid JSON from docker: {e}. stdout={process.stdout[:200]}",
                    }

            finally:
                # Clean up temp file
                try:
                    os.unlink(code_file)
                except:
                    pass

        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "error": "Extraction timeout exceeded",
            }
        except json.JSONDecodeError:
            return {
                "status": "error",
                "error": "Invalid JSON output from extraction",
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }
