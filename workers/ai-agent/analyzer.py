import re
import logging
import os
from pathlib import Path
from typing import Dict, Any, List
from crewai import Crew

from agents import create_policy_agent, create_analyzer_agent, create_decision_agent
from tasks import create_policy_task, create_analyzer_task, create_decision_task
from tools import CodeExecutorTool, PolicyLoaderTool
from schemas import AnalysisDecision, ExecutionResult, CodeViolation

logger = logging.getLogger(__name__)


def _analyze_code_for_pii_violations(repo_path: str, pii_fields: List[str]) -> tuple[List[CodeViolation], List[str]]:
    """Analyze source code for PII access patterns and return detailed violations"""
    violations = []
    analyzed_files = []
    
    # PII field patterns to detect
    pii_patterns = pii_fields if pii_fields else [
        'patient_id', 'patient_name', 'patient_identifier',
        'user_id', 'user_name', 'username', 'email', 'email_address', 
        'phone', 'phone_number', 'mobile', 'telephone',
        'ssn', 'social_security', 'social_security_number',
        'address', 'street_address', 'home_address',
        'credit_card', 'card_number', 'account_number',
        'passport', 'driver_license', 'license_number',
        'date_of_birth', 'birth_date', 'dob',
        'medical_record', 'mrn', 'medical_record_number',
        'insurance_id', 'policy_number',
        'name', 'first_name', 'last_name', 'full_name'
    ]
    
    # Find Python files to analyze (skip archetypes and build/archetypes folders)
    repo_path_obj = Path(repo_path)
    python_files = []
    for file_path in repo_path_obj.rglob("*.py"):
        # Skip files in archetypes and build/archetypes directories
        relative_path = str(file_path.relative_to(repo_path_obj))
        if not (relative_path.startswith("archetypes/") or relative_path.startswith("build/archetypes/")):
            python_files.append(file_path)
    
    for file_path in python_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            analyzed_files.append(str(file_path))
            lines = content.split('\n')
            
            for line_num, line in enumerate(lines, 1):
                line_lower = line.lower().strip()
                
                # Skip comments and empty lines
                if not line_lower or line_lower.startswith('#'):
                    continue
                
                # Check for PII field access patterns
                for pii_field in pii_patterns:
                    pii_field_lower = pii_field.lower()
                    
                    # Look for attribute access (e.g., patient.patient_id)
                    if f'.{pii_field_lower}' in line_lower:
                        violations.append(CodeViolation(
                            file=str(file_path.relative_to(repo_path_obj)),
                            line=line_num,
                            field=pii_field,
                            code=line.strip(),
                            type="attribute_access"
                        ))
                    
                    # Look for dictionary/bracket access (e.g., data['patient_id'])  
                    elif (f"'{pii_field_lower}'" in line_lower or f'"{pii_field_lower}"' in line_lower) and '[' in line and ']' in line:
                        violations.append(CodeViolation(
                            file=str(file_path.relative_to(repo_path_obj)),
                            line=line_num,
                            field=pii_field,
                            code=line.strip(),
                            type="dictionary_access"
                        ))
                    
                    # Look for print statements with PII
                    elif 'print' in line_lower and pii_field_lower in line_lower:
                        violations.append(CodeViolation(
                            file=str(file_path.relative_to(repo_path_obj)),
                            line=line_num,
                            field=pii_field,
                            code=line.strip(),
                            type="print_statement"
                        ))
                        
        except Exception as e:
            logger.warning(f"Error analyzing file {file_path}: {e}")
            continue
    
    return violations, analyzed_files


def analyze_repository(repo_path: str,  job_id: str) -> AnalysisDecision:
    """
    Analyze repository using CrewAI agents
    
    Args:
        repo_path: Path to cloned repository
        job_id: Job identifier
        
    Returns:
        AnalysisDecision with approval/rejection
    """
    logger.info(f"Starting CrewAI analysis for job {job_id}")
    
    try:
        # Initialize tools exactly like epsilon-airflow
        policy_tool = PolicyLoaderTool()
        executor_tool = CodeExecutorTool()
        
        # Create agents with tools
        policy_agent = create_policy_agent(policy_tool)
        analyzer_agent = create_analyzer_agent()  # No executor tool - analyzes pre-executed results
        decision_agent = create_decision_agent()
        
        # Step 1: Load policy
        policy = policy_tool._run()
        logger.info(f"Loaded policy: {policy['name']}")
        
        # Step 2: Execute code
        execution_result = executor_tool._run(repo_path, job_id)
        logger.info(f"Code execution: {'SUCCESS' if execution_result.success else 'FAILED'}")
        
        # Step 3: Perform detailed code analysis for PII violations
        code_violations, analyzed_files = _analyze_code_for_pii_violations(repo_path, policy["pii_fields"])
        logger.info(f"Code analysis found {len(code_violations)} PII violations in {len(analyzed_files)} files")
        
        # Step 4: Create tasks
        policy_task = create_policy_task(policy_agent, policy["pii_fields"])
        analyzer_task = create_analyzer_task(analyzer_agent, execution_result, policy["pii_fields"])
        decision_task = create_decision_task(decision_agent, job_id)
        
        # Step 5: Create and run crew
        crew = Crew(
            agents=[policy_agent, analyzer_agent, decision_agent],
            tasks=[policy_task, analyzer_task, decision_task],
            verbose=True
        )
        
        logger.info("Running CrewAI analysis...")
        result = crew.kickoff()
        
        logger.info("CrewAI analysis completed")
        
        # Step 6: Parse the decision result
        decision = _parse_crew_result(str(result))
        
        # Step 7: Add detected code violations to the decision
        decision.pii_details = code_violations
        decision.analyzed_files = analyzed_files
        
        logger.info(f"Final decision: {'APPROVED' if decision.approved else 'REJECTED'}")
        logger.info(f"Confidence: {decision.confidence_score}")
        logger.info(f"Reasoning: {decision.reasoning}")
        logger.info(f"Code violations: {len(code_violations)}")
        
        return decision
        
    except Exception as e:
        logger.error(f"CrewAI analysis failed: {e}")
        
        # Return conservative rejection on error
        return AnalysisDecision(
            approved=False,
            confidence_score=0.9,
            reasoning=f"Analysis failed due to error: {str(e)}",
            risks_identified=["analysis_error", "system_failure"],
            recommendations=["Manual review required", "Fix system issues and retry"]
        )


def _parse_crew_result(result_text: str) -> AnalysisDecision:
    """Parse CrewAI result into AnalysisDecision"""
    
    # Default values
    approved = False
    confidence_score = 0.5
    reasoning = "Unable to parse decision"
    risks_identified = []
    recommendations = []
    
    try:
        # Extract decision
        decision_match = re.search(r'DECISION:\s*(APPROVE|REJECT)', result_text, re.IGNORECASE)
        if decision_match:
            approved = decision_match.group(1).upper() == 'APPROVE'
            
        # Extract confidence
        confidence_match = re.search(r'CONFIDENCE:\s*([\d.]+)', result_text)
        if confidence_match:
            confidence_score = min(1.0, max(0.0, float(confidence_match.group(1))))
            
        # Extract reasoning
        reasoning_match = re.search(r'REASONING:\s*([^\n]+)', result_text)
        if reasoning_match:
            reasoning = reasoning_match.group(1).strip()
            
        # Extract risks
        risks_match = re.search(r'RISKS:\s*([^\n]+)', result_text)
        if risks_match:
            risks_text = risks_match.group(1).strip()
            if risks_text and risks_text != "None":
                risks_identified = [risk.strip() for risk in risks_text.split(',')]
                
        # Extract recommendations  
        rec_match = re.search(r'RECOMMENDATIONS:\s*([^\n]+)', result_text)
        if rec_match:
            rec_text = rec_match.group(1).strip()
            if rec_text and rec_text != "None":
                recommendations = [rec.strip() for rec in rec_text.split(',')]
                
    except Exception as e:
        logger.warning(f"Error parsing crew result: {e}")
        
    return AnalysisDecision(
        approved=approved,
        confidence_score=confidence_score,
        reasoning=reasoning,
        risks_identified=risks_identified,
        recommendations=recommendations
    )