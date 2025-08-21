from crewai import Task, Agent
from schemas import ExecutionResult
from typing import Dict, Any


def create_analyzer_task(agent: Agent, execution_result: ExecutionResult, pii_fields: list) -> Task:
    """Create code analysis task"""
    
    return Task(
        description=f"""
        IMPORTANT: The code has ALREADY been executed. Do NOT execute any code or use any tools.
        Your job is to analyze the provided execution results below.
        
        EXECUTION RESULTS TO ANALYZE:
        
        Code execution details:
        - Success: {execution_result.success}
        - Return code: {execution_result.return_code}
        - Execution time: {execution_result.execution_time}s
        - Number of output files: {len(execution_result.output_files)}
        
        Standard output:
        {execution_result.stdout}
        
        Standard error:
        {execution_result.stderr}
        
        Output files generated:
        {[f['name'] for f in execution_result.output_files]}
        
        Output file contents (first 1000 chars each):
        {chr(10).join([f"--- {f['name']} ---{chr(10)}{f.get('content', '[No content available]')[:1000]}" for f in execution_result.output_files])}
        
        PII fields to check for:
        {', '.join(pii_fields)}
        
        Your analysis should:
        1. Check stdout/stderr above for any PII field names or values
        2. Examine the output file contents for PII exposure
        3. Look for patterns like SSN, email, phone numbers
        4. Identify any potential privacy violations
        5. Assess the risk level (LOW/MEDIUM/HIGH)
        
        Be thorough and conservative - err on the side of caution.
        Do NOT execute any code - only analyze the results provided above.
        """,
        agent=agent,
        expected_output="Detailed PII analysis with specific findings and risk assessment"
    )