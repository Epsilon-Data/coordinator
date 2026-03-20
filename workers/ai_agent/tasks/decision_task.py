from crewai import Task, Agent

from workers.ai_agent.schemas import AnalysisDecision


def create_decision_task(agent: Agent, job_id: str) -> Task:
    """Create final decision task based on scanner findings."""

    return Task(
        description=f"""
        Make the final security decision for job {job_id}.

        Use the Code Analyzer's review of the automated scanner findings to decide.

        ============================================================
        DECISION RULES (based on scanner findings)
        ============================================================

        APPROVE if ALL of these are true:
          - AST scanner reported ZERO findings (no blocked imports, no dangerous calls)
          - Output disclosure checker reported ZERO findings (no leakage detected)
          - Code execution succeeded (return code 0)
          - The analyzer confirmed the script is safe
          → Set confidence_score 0.9-1.0

        REJECT if ANY of these are true:
          - AST scanner found any 'critical' severity finding
          - AST scanner found blocked imports (network, subprocess, etc.)
          - AST scanner found dangerous function calls (eval, exec, os.system)
          - Output checker found credential leaks in stdout/files
          - Output checker found encoded data blocks (exfiltration risk)
          - Code execution failed (return code ≠ 0) for security-related reasons
          → Set confidence_score 0.8-1.0

        FLAG FOR CAUTION (approve with lower confidence) if:
          - Only 'medium' or 'low' findings from scanners
          - Unknown imports that aren't on the blocked list
          - Minor output disclosure concerns
          → Set confidence_score 0.6-0.8

        ============================================================
        WHAT YOU MUST NOT DO
        ============================================================

        - Do NOT invent findings that the automated scanners did not report
        - Do NOT flag standard research patterns as threats:
          * Importing from `generated.models` (Epsilon SDK) is ALWAYS safe
          * Using print() to display analysis results is NORMAL
          * Accessing patient data fields (age, heart_rate, etc.) is the PURPOSE
          * Writing CSV/JSON output files is expected behavior
        - Do NOT reject code just because it accesses patient data — that is
          literally what the research platform exists for

        ============================================================
        OUTPUT
        ============================================================

        - approved: true/false
        - confidence_score: 0.0-1.0
        - reasoning: 2-3 sentences referencing the actual scanner findings
        - risks_identified: list from scanner findings only (empty if clean)
        - recommendations: remediation steps if rejected, monitoring notes if approved
        - pii_details: violations from scanner findings only (empty if clean)
        """,
        agent=agent,
        expected_output="Final security decision based on automated scanner findings",
        output_pydantic=AnalysisDecision,
    )
