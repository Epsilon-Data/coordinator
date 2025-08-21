from crewai import Agent
from langchain_openai import ChatOpenAI


def create_analyzer_agent() -> Agent:
    """Create code analysis agent for pre-executed results"""
    
    return Agent(
        role="Code Analyzer",
        goal="Analyze pre-executed code output for PII exposure and privacy violations",
        backstory="""You are a security analyst specialized in detecting PII leakage 
        in data analysis outputs. You carefully examine execution results including stdout, 
        stderr, and output files to identify any potential privacy violations. The code has 
        already been executed - your job is to analyze the results, not execute code.""",
        tools=[],  # No tools needed - analyze provided execution results
        verbose=True
    )