import re
import logging
from pathlib import Path
from typing import Dict, Any
from crewai import Crew

from agents import create_policy_agent, create_analyzer_agent, create_decision_agent
from tasks import create_policy_task, create_analyzer_task, create_decision_task
from tools import CodeExecutorTool, PolicyLoaderTool
from schemas import AnalysisDecision, ExecutionResult

logger = logging.getLogger(__name__)


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
        
        logger.info(f"Final decision: {'APPROVED' if decision.approved else 'REJECTED'}")
        logger.info(f"Confidence: {decision.confidence_score}")
        logger.info(f"Reasoning: {decision.reasoning}")
        
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