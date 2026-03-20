import os

from crewai import Agent


def create_analyzer_agent() -> Agent:
    """Create code analysis agent that reviews automated scanner results"""

    return Agent(
        role="Security Findings Reviewer",
        goal="Review the results from the automated AST scanner and output disclosure checker, and provide an expert assessment of whether the findings represent genuine threats",
        backstory="""You are a senior security analyst who reviews automated scanner output
        for a healthcare research platform. You do NOT scan code yourself — deterministic
        tools have already done that. Your job is to review their factual findings and
        assess whether each finding represents a genuine security threat in the context
        of researcher-submitted analysis scripts running inside AWS Nitro Enclaves.
        You understand that researchers legitimately access patient data fields, print
        analysis results, and write output files. You never fabricate findings that the
        automated scanners did not report.""",
        tools=[],
        allow_delegation=False,
        max_iter=3,
        verbose=os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG",
        llm=os.getenv("CREWAI_LLM_MODEL", "gpt-4o-mini"),
    )
