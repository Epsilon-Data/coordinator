import logging
import re
import os
from pathlib import Path
from typing import Dict, Any, List
from crewai import Crew

from workers.ai_agent.agents import create_policy_agent, create_analyzer_agent, create_decision_agent
from workers.ai_agent.tasks import create_policy_task, create_analyzer_task, create_decision_task
from workers.ai_agent.tools import CodeExecutorTool, PolicyLoaderTool
from workers.ai_agent.schemas import AnalysisDecision, ExecutionResult, CodeViolation

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _get_main_script_content(repo_path: str) -> tuple[str, str]:
    """Get the main analysis script content and filename"""
    repo_path_obj = Path(repo_path)

    # Look for yml-defined script first
    build_folder = repo_path_obj / "build"
    if build_folder.exists():
        build_yml = build_folder / "build.yml"
        if build_yml.exists():
            try:
                import yaml
                with open(build_yml, 'r') as f:
                    config = yaml.safe_load(f)
                if config and 'analysis' in config and 'script_file' in config['analysis']:
                    script_file = config['analysis']['script_file']
                    script_path = repo_path_obj / script_file
                    if script_path.exists():
                        with open(script_path, 'r', encoding='utf-8') as f:
                            return f.read(), script_file
            except Exception as e:
                logger.warning(f"Error reading yml config: {e}")

    # Fallback to standard main files
    main_files = ["example_analysis.py", "analysis.py", "main.py", "run.py", "app.py"]
    for main_file in main_files:
        script_path = repo_path_obj / main_file
        if script_path.exists():
            try:
                with open(script_path, 'r', encoding='utf-8') as f:
                    return f.read(), main_file
            except Exception as e:
                logger.warning(f"Error reading {main_file}: {e}")
                continue

    return "", "No main script found"


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
        
        # Step 3: Get main script content for analysis
        main_script_content, script_filename = _get_main_script_content(repo_path)
        logger.info(f"Analyzing main script: {script_filename}")

        # Step 4: Create tasks - pass main script and execution results to LLM
        policy_task = create_policy_task(policy_agent, policy["pii_fields"])
        analyzer_task = create_analyzer_task(analyzer_agent, execution_result, policy["pii_fields"], main_script_content, script_filename)
        decision_task = create_decision_task(decision_agent, job_id)

        # Step 5: Create and run crew - let LLM do intelligent analysis
        crew = Crew(
            agents=[policy_agent, analyzer_agent, decision_agent],
            tasks=[policy_task, analyzer_task, decision_task],
            verbose=True
        )

        logger.info("Running CrewAI analysis with LLM-based PII detection...")
        result = crew.kickoff()

        logger.info("CrewAI analysis completed")

        # Step 6: Parse the decision result (LLM will provide PII violations if any)
        decision = _parse_crew_result(str(result))

        # Step 7: Set analyzed files to just the main script
        decision.analyzed_files = [script_filename] if script_filename != "No main script found" else []
        
        logger.info(f"Final decision: {'APPROVED' if decision.approved else 'REJECTED'}")
        logger.info(f"Confidence: {decision.confidence_score}")
        logger.info(f"Reasoning: {decision.reasoning}")
        logger.info(f"Analyzed files: {decision.analyzed_files}")
        
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