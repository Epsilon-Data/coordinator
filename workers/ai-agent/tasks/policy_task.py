from crewai import Task, Agent
from typing import List


def create_policy_task(agent: Agent, pii_fields: List[str]) -> Task:
    """Create policy collection task"""
    
    return Task(
        description=f"""
        Load and understand the privacy policy for this healthcare dataset analysis.
        
        The following fields are considered PII in healthcare data:
        {', '.join(pii_fields)}
        
        Your task is to:
        1. Confirm these PII fields are comprehensive for healthcare data
        2. Define what constitutes a privacy violation
        3. Set clear rules for approval/rejection
        
        Output a clear policy summary that will guide the analysis.
        """,
        agent=agent,
        expected_output="A clear policy summary with PII fields and approval rules"
    )