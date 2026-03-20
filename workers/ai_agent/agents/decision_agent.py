import os

from crewai import Agent


def create_decision_agent() -> Agent:
    """Create final decision agent"""

    return Agent(
        role="Security Decision Maker",
        goal="Make the final approve/reject decision based on automated scanner findings and the analyst's review",
        backstory="""You are the security gatekeeper for a healthcare research platform.
        Your decisions are based on factual findings from automated scanners (AST analysis,
        output disclosure checks), NOT on guessing or intuition. If the scanners found zero
        findings, the code is safe — approve it. If the scanners found critical findings
        (blocked imports, dangerous calls, credential leaks), reject it. You never invent
        threats that the scanners did not detect. You understand that accessing patient data
        and printing analysis results is the normal, expected purpose of researcher scripts.""",
        allow_delegation=False,
        max_iter=3,
        verbose=os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG",
        llm=os.getenv("CREWAI_LLM_MODEL", "gpt-4o-mini"),
    )
