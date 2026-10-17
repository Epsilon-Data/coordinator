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
          - AST scanner reported ZERO security findings
          - Output disclosure checker reported ZERO findings
          - The analyzer confirmed no data privacy violations
          - Script returns ONLY aggregate statistics, model outputs, or plots
          → Set confidence_score 0.9-1.0

        REJECT if ANY of these are true:
          - AST scanner found blocked imports or dangerous calls (SECURITY)
          - Output checker found credential leaks or encoded data (SECURITY)
          - Script returns individual-level PII fields in output (PRIVACY)
          - Script prints patient records with identifying info (PRIVACY)
          - Script writes raw patient data to files (PRIVACY)
          - Script returns groups smaller than 5 with sensitive data (PRIVACY)
          - Script concatenates or exposes PII fields in output (PRIVACY)
          → Set confidence_score 0.8-1.0

        FLAG FOR CAUTION (approve with lower confidence) if:
          - Only 'medium' or 'low' scanner findings
          - Quasi-identifiers that could potentially re-identify (PRIVACY)
          - Large output volumes that are hard to audit
          → Set confidence_score 0.6-0.8

        ============================================================
        IMPORTANT DISTINCTIONS
        ============================================================

        - ACCESSING PII for computation is ALLOWED (groupby, filter, aggregate)
        - RETURNING PII in output is NOT ALLOWED (the output leaves the enclave)
        - Importing from `generated.models` (Epsilon SDK) is ALWAYS safe
        - The question is: does the OUTPUT contain individual-level PII?

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
