import logging
from crewai import Crew

from shared.job_logger import JobLogger
from workers.ai_agent.agents import create_policy_agent, create_analyzer_agent, create_decision_agent
from workers.ai_agent.tasks import create_policy_task, create_analyzer_task, create_decision_task
from workers.ai_agent.tools import CodeExecutorTool, PolicyLoaderTool, ASTSecurityScanner, OutputDisclosureTool
from workers.ai_agent.schemas import AnalysisDecision
from workers.ai_agent.security_constants import CREWAI_OUTPUT_MAX_LENGTH
from workers.ai_agent.utils import find_main_script

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def analyze_repository(repo_path: str, job_id: str) -> AnalysisDecision:
    """
    Analyze repository using deterministic scanners + LLM reasoning.

    Flow:
    1. Execute code (CodeExecutorTool)
    2. AST scan the script (ASTSecurityScanner) — deterministic
    3. Check output for disclosure (OutputDisclosureTool) — deterministic
    4. Pass factual findings to LLM agents for final judgment
    """
    logger.info(f"Starting analysis for job {job_id}")

    try:
        # --- Phase 1: Data collection (deterministic) ---

        # Load policy
        policy_tool = PolicyLoaderTool(repo_path=repo_path)
        policy = policy_tool._run()
        logger.info(f"Loaded policy: {policy['name']}")

        # Execute the code
        executor_tool = CodeExecutorTool()
        execution_result = executor_tool._run(repo_path, job_id)
        logger.info(f"Code execution: {'SUCCESS' if execution_result.success else 'FAILED'}")

        # Get main script content
        main_script_content, script_filename = find_main_script(repo_path)
        logger.info(f"Analyzing main script: {script_filename}")

        # --- Phase 2: Deterministic scanning ---

        # AST security scan
        ast_scanner = ASTSecurityScanner()
        ast_report = ast_scanner._run(main_script_content, script_filename)
        logger.info(f"AST scan: {'SAFE' if ast_report.safe else f'{len(ast_report.findings)} finding(s)'}")

        # Output disclosure check
        output_checker = OutputDisclosureTool()
        output_report = output_checker._run(
            stdout=execution_result.stdout,
            stderr=execution_result.stderr,
            output_files=execution_result.output_files,
        )
        logger.info(f"Output check: {'SAFE' if output_report.safe else f'{len(output_report.findings)} finding(s)'}")

        # --- Phase 3: LLM reasoning over factual findings ---

        # Create agents
        policy_agent = create_policy_agent(policy_tool)
        analyzer_agent = create_analyzer_agent()
        decision_agent = create_decision_agent()

        # Create tasks — pass scanner results, not raw code
        policy_task = create_policy_task(policy_agent, policy["pii_fields"])
        analyzer_task = create_analyzer_task(
            analyzer_agent,
            execution_result,
            policy["pii_fields"],
            main_script_content,
            script_filename,
            ast_report=ast_report,
            output_report=output_report,
        )
        decision_task = create_decision_task(decision_agent, job_id)

        # Store CrewAI step logs in DB via job_logger
        _job_logger = JobLogger("AIAgentWorker")

        def _task_callback(task_output):
            """Store each CrewAI task completion with full output in job_logs."""
            try:
                agent_name = str(getattr(task_output, 'agent', 'Unknown'))
                raw_output = str(getattr(task_output, 'raw', ''))
                # Truncate very long outputs but keep enough for meaningful display
                output_text = raw_output[:CREWAI_OUTPUT_MAX_LENGTH] if len(raw_output) > CREWAI_OUTPUT_MAX_LENGTH else raw_output
                _job_logger.info(
                    job_id, "ai_crew_task",
                    f"[{agent_name}] {output_text}",
                    metadata={
                        "agent": agent_name,
                        "output_length": len(raw_output),
                        "truncated": len(raw_output) > CREWAI_OUTPUT_MAX_LENGTH,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to log CrewAI task: {e}")

        crew = Crew(
            agents=[policy_agent, analyzer_agent, decision_agent],
            tasks=[policy_task, analyzer_task, decision_task],
            verbose=True,
            task_callback=_task_callback
        )

        logger.info("Running LLM agents to reason over scanner findings...")
        _job_logger.info(job_id, "ai_crew_start", "CrewAI pipeline started: Policy Specialist → Code Analyzer → Decision Maker", progress=40)

        crew_output = crew.kickoff()

        _job_logger.info(job_id, "ai_crew_complete", "CrewAI pipeline completed", progress=85)
        logger.info("LLM analysis completed")

        # Extract structured decision
        decision = crew_output.pydantic
        if not isinstance(decision, AnalysisDecision):
            raise ValueError("CrewAI failed to produce structured AnalysisDecision")

        decision.analyzed_files = [script_filename] if script_filename != "No main script found" else []

        logger.info(f"Final decision: {'APPROVED' if decision.approved else 'REJECTED'}")
        logger.info(f"Confidence: {decision.confidence_score}")
        logger.info(f"Reasoning: {decision.reasoning}")

        return decision

    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        return AnalysisDecision(
            approved=False,
            confidence_score=0.9,
            reasoning=f"Analysis failed due to error: {str(e)}",
            risks_identified=["analysis_error", "system_failure"],
            recommendations=["Manual review required", "Fix system issues and retry"]
        )
