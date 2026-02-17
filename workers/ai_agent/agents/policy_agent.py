from crewai import Agent


def create_policy_agent(policy_tool) -> Agent:
    """Create policy collection agent"""
    
    return Agent(
        role="Policy Collector",
        goal="Collect and understand dataset privacy policies and PII field definitions",
        backstory="""You are a privacy policy expert responsible for understanding 
        what constitutes PII (Personally Identifiable Information) for healthcare datasets. 
        You know which fields should be considered sensitive and what privacy rules apply.""",
        tools=[policy_tool],
        verbose=True
    )