from crewai import Task, Agent


def create_decision_task(agent: Agent, job_id: str) -> Task:
    """Create final decision task"""
    
    return Task(
        description=f"""
        Make the final decision on whether to approve or reject job {job_id} for execution.
        
        Based on the policy analysis and PII detection results, you must decide:
        
        APPROVE if:
        - No PII was detected in stdout/stderr output
        - No PII was detected in output file contents
        - Code execution was successful (return code 0)
        - Only dummy/synthetic data found (like "text_value_XXX", synthetic IDs)
        
        REJECT if:
        - Any real PII was detected (actual names, emails, SSNs, real patient IDs)
        - Code execution failed (return code != 0)
        - Real sensitive patterns found in output
        
        IMPORTANT: Dummy/synthetic data like "text_value_734" or synthetic IDs are NOT PII.
        Only reject if you find ACTUAL personal information.
        
        Your decision must include:
        1. DECISION: APPROVE or REJECT
        2. CONFIDENCE: 0.0 to 1.0 (how certain you are)
        3. REASONING: Clear explanation for the decision
        4. RISKS: List of identified risks (if any)
        5. RECOMMENDATIONS: What should be done next
        
        Format your response as:
        DECISION: [APPROVE/REJECT]
        CONFIDENCE: [0.0-1.0]
        REASONING: [explanation]
        RISKS: [list of risks]
        RECOMMENDATIONS: [list of recommendations]
        """,
        agent=agent,
        expected_output="Final decision with APPROVE/REJECT, confidence score, reasoning, risks, and recommendations"
    )