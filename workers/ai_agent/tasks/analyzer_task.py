from crewai import Task, Agent
from workers.ai_agent.schemas import ExecutionResult
from typing import Dict, Any


def create_analyzer_task(agent: Agent, execution_result: ExecutionResult, pii_fields: list, main_script_content: str = "", script_filename: str = "") -> Task:
    """Create code analysis task"""

    return Task(
        description=f"""
        IMPORTANT: The code has ALREADY been executed. Do NOT execute any code or use any tools.
        Your job is to analyze the main analysis script and its execution results for ACTUAL PII exposure.

        MAIN ANALYSIS SCRIPT TO ANALYZE:
        Filename: {script_filename}

        Script content:
        {main_script_content[:2000]}{'...' if len(main_script_content) > 2000 else ''}

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

        CRITICAL: READ THE ACTUAL SCRIPT CODE CAREFULLY!

        Look at the actual script content provided above.
        Read line by line what fields it actually accesses.
        Do NOT assume - read what is actually written in the script.

        WHAT TO ANALYZE:
        1. SCRIPT CODE ACCESS: What fields does the Python code actually access?
           - Look for lines like `record.patient.FIELD_NAME`
           - Check if FIELD_NAME is in the PII list
           - Examples: `patient_id`, `name`, `email` = PII (BAD)
           - Examples: `heart_rate`, `age`, `diagnosis` = NOT PII (OK)

        2. EXECUTION OUTPUT: Does stdout contain actual PII values?
           - Patient ID numbers, names, emails, SSNs = PII (BAD)
           - Heart rates, ages, anonymous statistics = NOT PII (OK)
           - Dummy/synthetic values are OK if clearly synthetic

        3. SCHEMA vs ACCESS: Schema showing fields exist ≠ Script accessing them
           - Schema metadata showing PII field names = OK (just documentation)
           - Script code actually accessing PII fields = BAD (privacy violation)

        DECISION CRITERIA:
        - APPROVE: Script accesses only non-PII fields AND output contains no PII values
        - REJECT: Script accesses PII fields OR output contains actual PII values
        - REJECT: Patient identifiers, names, emails in code or output
        - APPROVE: Only aggregated stats, heart rates, or anonymized data

        Do NOT execute any code - only analyze the script and results provided above.
        """,
        agent=agent,
        expected_output="Intelligent PII analysis focusing on actual privacy risks, not dummy data usage"
    )