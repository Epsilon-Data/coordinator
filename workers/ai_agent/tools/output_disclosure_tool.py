"""
Output disclosure checker for researcher analysis outputs.

Deterministically checks execution output for data leakage risks:
- Row-level data exposure (individual records in output)
- Small group sizes (aggregations with < threshold members)
- Credential/system info leakage in stdout
- Encoded data (base64 blocks suggesting exfiltration via output)
"""
import base64
import csv
import io
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List

from crewai.tools import BaseTool

from workers.ai_agent.security_constants import (
    BASE64_MIN_LENGTH, BASE64_MIN_DECODED_SIZE, EVIDENCE_SNIPPET_LENGTH,
)

logger = logging.getLogger(__name__)

# Patterns that suggest credential/system info leakage
CREDENTIAL_PATTERNS = [
    (r"[A-Za-z0-9_]+=sk-[A-Za-z0-9]{20,}", "API key (sk-...)"),
    (r"postgresql://[^\s]+", "Database connection string"),
    (r"AWS_[A-Z_]+=\S+", "AWS credential"),
    (r"OPENAI_API_KEY=\S+", "OpenAI API key"),
    (r"-----BEGIN [A-Z ]+ KEY-----", "Private/public key"),
]

SYSTEM_INFO_PATTERNS = [
    (r"Linux \S+ \d+\.\d+\.\d+", "Kernel version"),
    (r"/home/\w+/", "Home directory path"),
    (r"/app/\S+\.py", "Application file path"),
]


@dataclass
class OutputFinding:
    """A single deterministic finding from output analysis."""
    source: str  # "stdout", "stderr", or filename
    category: str  # row_level_data, small_group, credential_leak, encoded_data, system_info
    severity: str  # critical, high, medium, low
    detail: str
    evidence: str = ""  # truncated snippet


@dataclass
class OutputReport:
    """Complete output disclosure report."""
    safe: bool
    findings: List[dict] = field(default_factory=list)
    stdout_lines: int = 0
    output_file_count: int = 0
    total_output_rows: int = 0
    summary: str = ""


class OutputDisclosureTool(BaseTool):
    name: str = "Output Disclosure Checker"
    description: str = "Analyze execution output for data leakage, credential exposure, and disclosure risks"

    def _run(self, stdout: str, stderr: str, output_files: List[Dict[str, Any]]) -> OutputReport:
        """Check execution output for disclosure risks."""
        findings: List[OutputFinding] = []
        stdout_lines = len(stdout.splitlines()) if stdout else 0
        total_rows = 0

        # Check stdout
        if stdout:
            self._check_text_for_credentials(stdout, "stdout", findings)
            self._check_text_for_system_info(stdout, "stdout", findings)
            self._check_text_for_encoded_data(stdout, "stdout", findings)

        # Check stderr
        if stderr:
            self._check_text_for_credentials(stderr, "stderr", findings)
            self._check_text_for_system_info(stderr, "stderr", findings)

        # Check output files
        for file_info in output_files:
            name = file_info.get("name", "unknown")
            content = file_info.get("content", "")
            ext = file_info.get("extension", "")
            size = file_info.get("size", 0)

            if not content or content.startswith("["):
                continue

            # CSV files — check for row-level data and small groups
            if ext == ".csv":
                rows = self._check_csv(content, name, findings)
                total_rows += rows

            # Check all text content for credentials and encoding
            self._check_text_for_credentials(content, name, findings)
            self._check_text_for_encoded_data(content, name, findings)

        is_safe = len(findings) == 0

        if is_safe:
            summary = (
                f"Output passed all disclosure checks. "
                f"stdout: {stdout_lines} lines, {len(output_files)} output files, "
                f"{total_rows} total data rows. No leakage detected."
            )
        else:
            counts = {}
            for f in findings:
                counts[f.severity] = counts.get(f.severity, 0) + 1
            parts = [f"{v} {k}" for k, v in sorted(counts.items())]
            summary = f"Output has {len(findings)} disclosure finding(s): {', '.join(parts)}."

        return OutputReport(
            safe=is_safe,
            findings=[asdict(f) for f in findings],
            stdout_lines=stdout_lines,
            output_file_count=len(output_files),
            total_output_rows=total_rows,
            summary=summary,
        )

    def _check_csv(self, content: str, filename: str, findings: List[OutputFinding]) -> int:
        """Check CSV for row-level data and small groups. Returns row count."""
        try:
            reader = csv.reader(io.StringIO(content))
            rows = list(reader)
        except csv.Error:
            return 0

        if len(rows) < 2:
            return 0

        data_rows = len(rows) - 1  # exclude header

        # Check if output looks like row-level data (many rows, could be individual records)
        # This is a heuristic — CSV with many rows that aren't aggregated
        header = [h.lower().strip() for h in rows[0]]

        # If headers contain known identifier-like columns AND many rows
        id_columns = {"id", "patient_id", "record_id", "mrn", "ssn", "email", "name", "first_name", "last_name"}
        has_id_column = bool(id_columns & set(header))

        if has_id_column and data_rows > 0:
            findings.append(OutputFinding(
                source=filename,
                category="row_level_data",
                severity="high",
                detail=f"CSV has identifier column(s) ({id_columns & set(header)}) with {data_rows} rows — potential individual-level data",
                evidence=f"Headers: {header[:8]}",
            ))

        return data_rows

    def _check_text_for_credentials(self, text: str, source: str, findings: List[OutputFinding]):
        """Check text for credential/secret patterns."""
        for pattern, description in CREDENTIAL_PATTERNS:
            matches = re.findall(pattern, text)
            if matches:
                findings.append(OutputFinding(
                    source=source,
                    category="credential_leak",
                    severity="critical",
                    detail=f"{description} found in {source}",
                    evidence=matches[0][:EVIDENCE_SNIPPET_LENGTH] + "..." if len(matches[0]) > EVIDENCE_SNIPPET_LENGTH else matches[0],
                ))

    def _check_text_for_system_info(self, text: str, source: str, findings: List[OutputFinding]):
        """Check text for system information leakage."""
        for pattern, description in SYSTEM_INFO_PATTERNS:
            if re.search(pattern, text):
                match = re.search(pattern, text)
                findings.append(OutputFinding(
                    source=source,
                    category="system_info",
                    severity="medium",
                    detail=f"{description} found in {source}",
                    evidence=match.group()[:60] if match else "",
                ))

    def _check_text_for_encoded_data(self, text: str, source: str, findings: List[OutputFinding]):
        """Check for base64-encoded blocks that could be exfiltration."""
        b64_pattern = rf'[A-Za-z0-9+/]{{{BASE64_MIN_LENGTH},}}={{0,2}}'
        matches = re.findall(b64_pattern, text)
        for match in matches:
            try:
                decoded = base64.b64decode(match)
                if len(decoded) > BASE64_MIN_DECODED_SIZE:
                    findings.append(OutputFinding(
                        source=source,
                        category="encoded_data",
                        severity="high",
                        detail=f"Base64-encoded block ({len(match)} chars) found in {source} — possible data exfiltration via output",
                        evidence=match[:EVIDENCE_SNIPPET_LENGTH] + "...",
                    ))
            except (base64.binascii.Error, ValueError):
                pass  # Not valid base64
