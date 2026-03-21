import json
from typing import List, Optional

from crewai import Task, Agent

from workers.ai_agent.schemas import ExecutionResult
from workers.ai_agent.tools.ast_scanner_tool import ScanReport
from workers.ai_agent.tools.output_disclosure_tool import OutputReport


def create_analyzer_task(
    agent: Agent,
    execution_result: ExecutionResult,
    pii_fields: List[str],
    main_script_content: str = "",
    script_filename: str = "",
    ast_report: Optional[ScanReport] = None,
    output_report: Optional[OutputReport] = None,
) -> Task:
    """Create code analysis task using deterministic scanner results."""

    # Format AST findings
    if ast_report:
        ast_section = f"""
        AST SCAN RESULT: {'SAFE — no findings' if ast_report.safe else 'FINDINGS DETECTED'}
        Summary: {ast_report.summary}
        Total lines scanned: {ast_report.total_lines}
        Imports found: {ast_report.imports_found}
        Blocked imports: {ast_report.blocked_imports_found if ast_report.blocked_imports_found else 'NONE'}
        Safe imports: {ast_report.safe_imports_found}
        Unknown imports: {ast_report.unknown_imports if ast_report.unknown_imports else 'NONE'}
        Has network access: {ast_report.has_network_access}
        Has code injection: {ast_report.has_code_injection}
        Has credential access: {ast_report.has_credential_access}
        Has filesystem escape: {ast_report.has_filesystem_escape}
        Has obfuscation: {ast_report.has_obfuscation}

        DETAILED FINDINGS:
        {json.dumps(ast_report.findings, indent=2) if ast_report.findings else 'No findings — all checks passed.'}
        """
    else:
        ast_section = "AST scan not available."

    # Format output disclosure findings
    if output_report:
        output_section = f"""
        OUTPUT DISCLOSURE CHECK: {'SAFE — no findings' if output_report.safe else 'FINDINGS DETECTED'}
        Summary: {output_report.summary}
        Stdout lines: {output_report.stdout_lines}
        Output files: {output_report.output_file_count}
        Total data rows: {output_report.total_output_rows}

        DETAILED FINDINGS:
        {json.dumps(output_report.findings, indent=2) if output_report.findings else 'No findings — output is clean.'}
        """
    else:
        output_section = "Output disclosure check not available."

    return Task(
        description=f"""
        Review the automated security scanner results for script '{script_filename}'
        and provide your expert assessment.

        IMPORTANT: The automated scanners have already analyzed the code and output.
        Your job is to REVIEW THEIR FINDINGS, not re-scan the code yourself.
        Trust the scanner results — they are deterministic and factual.

        ============================================================
        AUTOMATED AST SECURITY SCAN RESULTS
        ============================================================
        {ast_section}

        ============================================================
        AUTOMATED OUTPUT DISCLOSURE CHECK RESULTS
        ============================================================
        {output_section}

        ============================================================
        EXECUTION CONTEXT
        ============================================================
        Success: {execution_result.success}
        Return code: {execution_result.return_code}
        Execution time: {execution_result.execution_time}s
        Output files: {[f['name'] for f in execution_result.output_files]}

        ============================================================
        SCRIPT SOURCE (for context only — AST scanner already analyzed this)
        ============================================================
        ```python
        {main_script_content[:2000]}{'...[truncated]' if len(main_script_content) > 2000 else ''}
        ```

        ============================================================
        YOUR TASK
        ============================================================

        Based on the scanner results above:

        1. If AST scan found ZERO findings and output check found ZERO findings:
           → The script is safe. State this clearly. Do not invent new findings.

        2. If scanners found findings:
           → Assess whether each finding is a genuine threat or acceptable.
           → Consider the context: this is a researcher analysis script running
             in a Trusted Execution Environment (AWS Nitro Enclave).
           → The `generated.models` module is the Epsilon SDK's auto-generated
             data loader — it is ALWAYS safe and expected.
           → Printing analysis results (counts, statistics, field values) is
             NORMAL behavior for research scripts.

        3. Provide your assessment as a structured summary that the Decision
           Agent can use.

        CRITICAL RULE: Do NOT fabricate findings that the scanners did not report.
        If the scanners say "no findings", your answer must confirm the script is safe.
        """,
        agent=agent,
        expected_output="Assessment of scanner findings: confirm safety if no findings, or explain genuine threats if findings exist"
    )
