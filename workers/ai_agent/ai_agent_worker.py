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
from workers.ai_agent.analyzer import analyze_repository
from workers.ai_agent.schemas import AnalysisDecision

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AIAgentWorker(AIWorkerBase):
    """Worker responsible for AI analysis of repositories using CrewAI"""

    def __init__(self):
        super().__init__("AIAgentWorker")

        try:
            self.analysis_path = Path(config.shared_storage_path) / "ai_analysis"
            self.analysis_path.mkdir(parents=True, exist_ok=True)
            self._setup_environment()
            self.job_logger = JobLogger(self.worker_name)
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            sys.exit(1)

    def _setup_environment(self) -> None:
        """Set up environment variables for CrewAI."""
        if not os.getenv("OPENAI_API_KEY"):
            logger.error("OPENAI_API_KEY not set")
            raise ValueError("OPENAI_API_KEY environment variable is required")

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

        try:
            # Log start
            self.job_logger.info(
                job_id, "ai_analysis",
                f"Starting AI security analysis",
                metadata={"repo_path": str(repo_path), "workspace_id": workspace_id}
            )

            # Run analysis
            self.job_logger.info(job_id, "ai_analysis", "Running AI agents", progress=30)

            decision = analyze_repository(repo_path=str(repo_path), job_id=job_id)

            logger.info(f"Analysis completed: {'APPROVED' if decision.approved else 'REJECTED'}")

            # Save results
            result_path = self._save_analysis_result(job_id, decision)

            if decision.approved:
                self.job_logger.info(
                    job_id, "ai_approved",
                    f"Repository approved (confidence: {decision.confidence_score})",
                    metadata={
                        "confidence_score": decision.confidence_score,
                        "reasoning": decision.reasoning,
                        "result_path": str(result_path)
                    },
                    progress=100
                )

                job_repository.update_job_status(
                    job_id=job_id,
                    status='ai_approved',
                    ai_confidence_score=decision.confidence_score,
                    ai_reasoning=decision.reasoning,
                    ai_result_path=str(result_path)
                )
                logger.info(f"Job {job_id} approved")

            else:
                self.job_logger.info(
                    job_id, "ai_rejected",
                    f"Repository rejected: {decision.reasoning}",
                    metadata={
                        "confidence_score": decision.confidence_score,
                        "reasoning": decision.reasoning,
                        "risks_identified": decision.risks_identified,
                        "pii_details": [v.model_dump() for v in decision.pii_details] if decision.pii_details else [],
                        "result_path": str(result_path)
                    },
                    progress=100
                )

                job_repository.update_job_status(
                    job_id=job_id,
                    status='ai_rejected',
                    ai_confidence_score=decision.confidence_score,
                    ai_reasoning=decision.reasoning,
                    ai_risks=decision.risks_identified,
                    ai_result_path=str(result_path)
                )
                logger.info(f"Job {job_id} rejected: {decision.reasoning}")

        except Exception as e:
            logger.error(f"AI analysis for job {job_id} failed: {e}")
            self.job_logger.error(
                job_id, "ai_analysis",
                f"AI analysis failed: {e}",
                error=e,
                metadata={"repo_path": str(repo_path)}
            )
            self._update_job_failed(job_id, str(e))

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
