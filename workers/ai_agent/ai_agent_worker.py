import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from shared import config
from shared.db import job_repository
from shared.job_logger import JobLogger
from shared.base_worker import AIWorkerBase
from shared.process_hardening import deny_same_user_inspection
from workers.ai_agent.access_gate import ReviewAccess, ReviewAccessGate
from workers.ai_agent.analyzer import analyze_repository
from workers.ai_agent.schemas import AnalysisDecision

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Stored as the reasoning of a review that did not run; the Research Workspace shows
# reasoning with this prefix as "AI review unavailable"
REVIEW_NOT_RUN_PREFIX = "AI analysis error: the review was not run"


class AIAgentWorker(AIWorkerBase):
    """Worker responsible for AI analysis of repositories using CrewAI"""

    def __init__(self):
        super().__init__("AIAgentWorker")

        try:
            self.analysis_path = Path(config.shared_storage_path) / "ai_analysis"
            self.analysis_path.mkdir(parents=True, exist_ok=True)
            self._setup_environment()
            self.job_logger = JobLogger(self.worker_name)
            self.access_gate = ReviewAccessGate.from_environment()
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            sys.exit(1)

    def _setup_environment(self) -> None:
        """Set up environment variables for CrewAI."""
        if not os.getenv("OPENAI_API_KEY"):
            logger.error("OPENAI_API_KEY not set")
            raise ValueError("OPENAI_API_KEY environment variable is required")
        # The review executes submitted code as a child of this process: it must not be
        # able to read this process's environment or memory
        if deny_same_user_inspection():
            logger.info("Worker process marked not dumpable")
        elif sys.platform.startswith("linux"):
            raise ValueError("the worker process could not be protected from the code it executes")

    def _save_analysis_result(self, job_id: str, decision: AnalysisDecision) -> Path:
        """Save analysis results to file"""
        result_path = self.analysis_path / job_id / "analysis_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)

        result_data = {
            "approved": decision.approved,
            "confidence_score": decision.confidence_score,
            "reasoning": decision.reasoning,
            "risks_identified": decision.risks_identified,
            "recommendations": decision.recommendations,
            "pii_details": [v.model_dump() for v in decision.pii_details] if decision.pii_details else [],
            "analyzed_files": decision.analyzed_files or [],
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        with open(result_path, 'w') as f:
            json.dump(result_data, f, indent=2)

        return result_path

    def process_message(self, message: Dict[str, Any]) -> None:
        """Process an AI analysis job."""
        job_id = message['job_id']
        repo_path = Path(message['repo_path'])
        workspace_id = message.get('workspace_id')
        metadata = message.get('metadata', {})

        logger.info(f"Processing AI analysis for job {job_id}")

        # No code of the job is executed unless its submitter is approved for its project
        access = self.access_gate.decide(job_id, str(repo_path))
        if not access.allowed:
            self._skip_review(job_id, access)
            return

        try:
            # Log start
            self.job_logger.info(
                job_id, "ai_analysis",
                f"Starting AI security analysis",
                metadata={"repo_path": str(repo_path), "workspace_id": workspace_id},
                progress=0
            )

            # Step 1: Code scanning
            self.job_logger.info(job_id, "ai_scanning", "Scanning code with AST analyzer", progress=15)

            # Step 2: Run AI agents
            self.job_logger.info(job_id, "ai_agents", "Running CrewAI security agents (Policy Specialist → Code Analyzer → Decision Maker)", progress=30)

            decision = analyze_repository(repo_path=str(repo_path), job_id=job_id)

            # Step 3: Analysis complete
            self.job_logger.info(
                job_id, "ai_complete",
                f"AI analysis completed: {'APPROVED' if decision.approved else 'FLAGGED'} (confidence: {decision.confidence_score})",
                progress=90
            )

            logger.info(f"Analysis completed: {'APPROVED' if decision.approved else 'REJECTED'}")

            # Save results
            result_path = self._save_analysis_result(job_id, decision)

            # AI analysis is independent — writes results as metadata
            # without changing the job status (executor picks up 'cloned' directly)
            ai_status = 'ai_approved' if decision.approved else 'ai_rejected'

            self.job_logger.info(
                job_id, ai_status,
                f"Repository {'approved' if decision.approved else 'rejected'} (confidence: {decision.confidence_score})",
                metadata={
                    "confidence_score": decision.confidence_score,
                    "reasoning": decision.reasoning,
                    "risks_identified": decision.risks_identified if not decision.approved else [],
                    "result_path": str(result_path)
                },
                progress=100
            )

            # Store AI results as metadata only — do NOT change job status
            job_repository.update_job_ai_results(
                job_id=job_id,
                ai_confidence_score=decision.confidence_score,
                ai_reasoning=decision.reasoning,
                ai_risks=decision.risks_identified if not decision.approved else None,
                ai_result_path=str(result_path),
                ai_decision=ai_status
            )
            logger.info(f"Job {job_id} AI analysis: {ai_status} (independent, status unchanged)")

        except Exception as e:
            logger.error(f"AI analysis for job {job_id} failed: {e}", exc_info=True)
            self.job_logger.error(
                job_id, "ai_error",
                f"AI analysis failed: {e}",
                error=e,
                metadata={"repo_path": str(repo_path), "error_type": type(e).__name__}
            )
            # Store error as AI result (informational, doesn't change job status)
            try:
                job_repository.update_job_ai_results(
                    job_id=job_id,
                    ai_confidence_score=0.0,
                    ai_reasoning=f"AI analysis error: {e}",
                    ai_decision="ai_error"
                )
            except Exception as store_err:
                logger.error(f"Failed to store AI error for {job_id}: {store_err}")

    def _skip_review(self, job_id: str, access) -> None:
        """Record that the review did not run. Nothing of the job was executed."""
        logger.warning(f"AI review of job {job_id} not run: {access.reason}")
        if access.status == ReviewAccess.UNDECIDED:
            # Left unanalysed: a later poll asks again, until the executor ends the job
            return
        try:
            job_repository.update_job_ai_results(
                job_id=job_id,
                ai_confidence_score=0.0,
                ai_reasoning=f"{REVIEW_NOT_RUN_PREFIX}: {access.reason}",
                ai_decision="ai_error",
            )
        except Exception as store_err:
            logger.error(f"Failed to store the skipped AI review for {job_id}: {store_err}")

    def process_job(self, job: Dict[str, Any]) -> bool:
        """Process a single AI analysis job."""
        job_id = job['job_id']
        repo_path = f"{config.shared_storage_path}/repositories/{job_id}"

        message = {
            'job_id': job_id,
            'repo_path': repo_path,
            'workspace_id': str(job['workspace_id']) if job.get('workspace_id') else None,
            'github_repo': job['github_repo'],
            'branch': job['github_branch'],
            'commit_sha': job['commit_sha'],
            'metadata': {}
        }

        try:
            self.process_message(message)
            logger.info(f"Successfully processed job {job_id}")
            return True
        except Exception as e:
            logger.error(f"Error processing job {job_id}: {e}")
            self._update_job_failed(job_id, str(e))
            return False

    def _health_check(self) -> None:
        """Perform worker-specific health checks."""
        super()._health_check()
        if not self.analysis_path.exists():
            raise RuntimeError(f"AI analysis directory does not exist: {self.analysis_path}")


def main() -> None:
    """Main entry point."""
    worker = AIAgentWorker()

    try:
        worker.run()
    except KeyboardInterrupt:
        worker.shutdown()


if __name__ == "__main__":
    main()
