from crewai import Task, Agent


def create_decision_task(agent: Agent, job_id: str) -> Task:
    """Create final decision task"""
    
    return Task(
        description=f"""
        Make the final decision on whether to approve or reject job {job_id} for execution.
        
        Based on the policy analysis and PII detection results, you must decide:
        
        INTELLIGENT DECISION CRITERIA:

        APPROVE if:
        - Code execution was successful (return code 0)
        - Script only accesses non-PII fields (like heart_rate, age, diagnosis)
        - Execution output contains only aggregated/anonymized data
        - No actual PII values are exposed in output
        - Dummy data is used appropriately for testing

        REJECT if:
        - Code execution failed (return code != 0)
        - Script directly accesses PII fields (patient_id, ssn, email, etc.) from the actual code
        - Execution output exposes actual PII values (not schema definitions or dummy data)
        - Real identifiable information is leaked

        CONTEXT-AWARE ANALYSIS:
        - Schema definitions showing PII field names = OK (not actual access)
        - Dummy/synthetic data containing PII patterns = OK (not real exposure)
        - Code accessing non-PII fields like vitals = OK (legitimate analysis)
        - Code that would leak real PII if run on real datasets = REJECT
        
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