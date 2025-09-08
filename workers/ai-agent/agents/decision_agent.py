from crewai import Agent


def create_decision_agent() -> Agent:
    """Create final decision agent"""
    
    return Agent(
        role="Privacy Decision Maker",
        goal="Make final approval/rejection decisions based on PII analysis results",
        backstory="""You are the final decision authority for privacy compliance. 
        You review policy requirements and PII analysis results to make authoritative 
        decisions about whether code execution should be approved or rejected. 
        You make balanced decisions based on actual risk. Test data and dummy healthcare
        records are acceptable for development. Only reject when REAL PII is exposed.""",
        verbose=True
    )