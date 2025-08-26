import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any

# Add paths for imports
sys.path.append('/app')
sys.path.append('/app/workers/ai-agent')

from shared import BaseWorker, config, job_repository
from shared.job_logger import JobLogger, StepTypes
from analyzer import analyze_repository
from schemas import AnalysisDecision

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AIAgentWorker(BaseWorker):
    """Worker responsible for AI analysis of repositories using CrewAI"""
    
    def __init__(self):
        super().__init__(
            worker_name="AIAgentWorker",
            queue_name=config.ai_queue,
            routing_keys=[config.routing_key_cloned]
        )
        
        # Ensure AI analysis output directory exists
        self.analysis_path = Path(config.shared_storage_path) / "ai_analysis"
        self.analysis_path.mkdir(parents=True, exist_ok=True)
        
        # Set up environment for AI agent
        self._setup_environment()
        
        # Initialize job logger
        self.job_logger = JobLogger(self.worker_name)
        
    def _setup_environment(self):
        """Set up environment variables for CrewAI AI agent"""
        # OpenAI API key is required for CrewAI
        if not os.getenv("OPENAI_API_KEY"):
            logger.error("OPENAI_API_KEY not set - CrewAI AI agent will not work")
            raise ValueError("OPENAI_API_KEY environment variable is required")
            
    def _get_dummy_data_path(self, repo_path: Path) -> Path:
        """Get path to dummy data for the repository"""
        # Check if repository has its own dummy data
        repo_dummy_data = repo_path / "dummy_data"
        if repo_dummy_data.exists():
            return repo_dummy_data
            
        # Use archetypes from sdk-epsilon if available
        possible_archetype_paths = [
            Path("/app/sdk-epsilon/archetypes"),
            Path("/app/archetypes"),
            Path(config.shared_storage_path) / "archetypes"
        ]
        
        for archetype_path in possible_archetype_paths:
            if archetype_path.exists():
                logger.info(f"Using archetypes from: {archetype_path}")
                return archetype_path
                
        # Create empty dummy data directory as fallback
        dummy_path = self.analysis_path / "dummy_data"
        dummy_path.mkdir(exist_ok=True)
        logger.warning(f"No archetypes found, using empty dummy data: {dummy_path}")
        return dummy_path
        
    def _save_analysis_result(self, job_id: str, decision: AnalysisDecision) -> Path:
        """Save analysis results to file"""
        import json
        
        result_path = self.analysis_path / job_id / "analysis_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        
        result_data = {
            "approved": decision.approved,
            "confidence_score": decision.confidence_score,
            "reasoning": decision.reasoning,
            "risks_identified": decision.risks_identified,
            "recommendations": decision.recommendations,
            "pii_details": [violation.model_dump() for violation in decision.pii_details] if decision.pii_details else [],
            "analyzed_files": decision.analyzed_files or [],
            "timestamp": os.popen('date -u +"%Y-%m-%dT%H:%M:%SZ"').read().strip(),
            "analysis_version": "crewai-epsilon-coordinator-1.0"
        }
        
        with open(result_path, 'w') as f:
            json.dump(result_data, f, indent=2)
            
        return result_path
        
    def process_message(self, message: Dict[str, Any]):
        """Process an AI analysis job message using CrewAI"""
        job_id = message['job_id']
        repo_path = Path(message['repo_path'])
        workspace_id = message.get('workspace_id')
        metadata = message.get('metadata', {})
        
        logger.info(f"Processing CrewAI analysis for job {job_id}")
        logger.info(f"Repository path: {repo_path}")
        
        try:
            # Log AI analysis start
            parent_log_id = self.job_logger.log_step_start(
                job_id=job_id,
                step_name="AI Security Analysis",
                step_type=StepTypes.AI_START,
                message=f"Starting AI security analysis for repository",
                metadata={
                    "repo_path": str(repo_path),
                    "workspace_id": workspace_id,
                    "main_language": metadata.get("main_language"),
                    "files_count": metadata.get("files_count", 0),
                    "crewai_version": "epsilon-coordinator-1.0"
                }
            )
            
            # Update job status to analyzing
            job_repository.update_job_status(
                job_id=job_id,
                status='analyzing'
            )
            
            # Log analysis preparation
            self.job_logger.log_step_progress(
                job_id=job_id,
                step_name="Prepare Analysis",
                step_type=StepTypes.AI_ANALYZING,
                message="Preparing repository for AI analysis",
                progress=10,
                metadata={
                    "repo_path": str(repo_path),
                    "main_language": metadata.get("main_language")
                },
                parent_log_id=parent_log_id
            )
            
            # Keep old log for backward compatibility
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message="Starting CrewAI analysis",
                metadata={
                    "repo_path": str(repo_path),
                    "main_language": metadata.get("main_language"),
                    "crewai_version": "epsilon-coordinator-1.0"
                }
            )
            
            # Log analysis execution
            self.job_logger.log_step_progress(
                job_id=job_id,
                step_name="Execute Analysis",
                step_type=StepTypes.AI_ANALYZING,
                message="Running AI agents to analyze code security and compliance",
                progress=30,
                parent_log_id=parent_log_id
            )
            
            # Run CrewAI analysis using our implementation
            logger.info(f"Running CrewAI analyze_repository for {repo_path}")
            decision = analyze_repository(
                repo_path=str(repo_path),
                # dummy_data_path=str(dummy_data_path),
                job_id=job_id
            )
            
            logger.info(f"CrewAI analysis completed: {'APPROVED' if decision.approved else 'REJECTED'}")
            logger.info(f"Confidence: {decision.confidence_score}, Reasoning: {decision.reasoning}")
            
            # Save analysis results
            result_path = self._save_analysis_result(job_id, decision)
            
            # Log analysis results
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message=f"CrewAI analysis completed: {'Approved' if decision.approved else 'Rejected'}",
                metadata={
                    "approved": decision.approved,
                    "confidence_score": decision.confidence_score,
                    "reasoning": decision.reasoning,
                    "risks_identified": decision.risks_identified,
                    "recommendations": decision.recommendations,
                    "pii_details": [violation.model_dump() for violation in decision.pii_details] if decision.pii_details else [],
                    "analyzed_files": decision.analyzed_files or [],
                    "result_path": str(result_path)
                }
            )
            
            if decision.approved:
                # Log AI approval
                self.job_logger.log_step_complete(
                    job_id=job_id,
                    step_name="AI Approved",
                    step_type=StepTypes.AI_APPROVED,
                    message=f"Repository approved with confidence score: {decision.confidence_score}",
                    metadata={
                        "approved": True,
                        "confidence_score": decision.confidence_score,
                        "reasoning": decision.reasoning,
                        "result_path": str(result_path)
                    },
                    parent_log_id=parent_log_id
                )
                
                # Update job status to AI approved
                job_repository.update_job_status(
                    job_id=job_id,
                    status='ai_approved',
                    ai_confidence_score=decision.confidence_score,
                    ai_reasoning=decision.reasoning,
                    ai_result_path=str(result_path)
                )
                
                # Publish approval message (only in RabbitMQ mode)
                if config.job_fetch_mode != "polling":
                    self.publish_message(
                        routing_key=config.routing_key_approved,
                        message={
                            'job_id': job_id,
                            'repo_path': str(repo_path),
                            'workspace_id': workspace_id,
                            'ai_decision': {
                                'approved': True,
                                'confidence_score': decision.confidence_score,
                                'reasoning': decision.reasoning,
                                'analysis_method': 'crewai-epsilon-coordinator'
                            }
                        }
                    )
                
                logger.info(f"Job {job_id} approved by CrewAI analysis")
                
            else:
                # Log AI rejection
                self.job_logger.log_step_complete(
                    job_id=job_id,
                    step_name="AI Rejected",
                    step_type=StepTypes.AI_REJECTED,
                    message=f"Repository rejected: {decision.reasoning}",
                    metadata={
                        "approved": False,
                        "confidence_score": decision.confidence_score,
                        "reasoning": decision.reasoning,
                        "risks_identified": decision.risks_identified,
                        "recommendations": decision.recommendations,
                        "pii_details": [violation.model_dump() for violation in decision.pii_details] if decision.pii_details else [],
                        "analyzed_files": decision.analyzed_files or [],
                        "result_path": str(result_path)
                    },
                    parent_log_id=parent_log_id
                )
                
                # Update job status to AI rejected
                job_repository.update_job_status(
                    job_id=job_id,
                    status='ai_rejected',
                    ai_confidence_score=decision.confidence_score,
                    ai_reasoning=decision.reasoning,
                    ai_risks=decision.risks_identified,
                    ai_result_path=str(result_path)
                )
                
                # Publish rejection message (only in RabbitMQ mode)
                if config.job_fetch_mode != "polling":
                    self.publish_message(
                        routing_key=config.routing_key_rejected,
                        message={
                            'job_id': job_id,
                            'reason': decision.reasoning,
                            'risks': decision.risks_identified,
                            'recommendations': decision.recommendations,
                            'confidence_score': decision.confidence_score,
                            'analysis_method': 'crewai-epsilon-coordinator'
                        }
                    )
                
                logger.info(f"Job {job_id} rejected by CrewAI analysis: {decision.reasoning}")
                
        except Exception as e:
            logger.error(f"CrewAI analysis for job {job_id} failed: {e}")
            
            # Log the error in detailed logs
            self.job_logger.log_step_error(
                job_id=job_id,
                step_name="AI Analysis Failed",
                step_type=StepTypes.FAILED,
                message=f"AI analysis failed: {str(e)}",
                error=e,
                metadata={
                    "repo_path": str(repo_path),
                    "workspace_id": workspace_id,
                    "error_type": type(e).__name__
                },
                parent_log_id=parent_log_id if 'parent_log_id' in locals() else None
            )
            
            # Keep old log for backward compatibility
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message=f"CrewAI analysis failed: {str(e)}",
                level="error",
                metadata={
                    "error_type": type(e).__name__,
                    "error_details": str(e),
                    "repo_path": str(repo_path)
                }
            )
            
            raise  # Re-raise to let base worker handle the error


    def _poll_for_jobs(self):
        """Poll database for jobs with cloned status"""
        try:
            # Get jobs with full data from database including workspace info and repo_path
            with job_repository.db.get_cursor() as cursor:
                query = """
                    SELECT jr.job_id, jr.workspace_id, jr.user_id, jr.commit_sha, 
                           jr.commit_message, jr.commit_author, jr.created_at, jr.status,
                           jr.result_metadata as repo_path, jr.logs as repo_metadata,
                           w.github_repo, w.github_branch
                    FROM job_requests jr
                    JOIN workspaces w ON jr.workspace_id = w.id
                    WHERE jr.status = 'cloned'
                    ORDER BY jr.created_at ASC
                    FOR UPDATE OF jr SKIP LOCKED
                """
                cursor.execute(query)
                jobs = cursor.fetchall()
            
            for job in jobs:
                logger.info(f"Found cloned job {job['job_id']} for AI analysis")
                
                # Get repo_path from result_metadata field
                repo_path = job.get('repo_path', '')
                if not repo_path:
                    # Fallback: construct repo path from workspace info
                    workspace_id = job.get('workspace_id')
                    if workspace_id:
                        repo_path = f"{config.shared_storage_path}/repositories/{job['job_id']}"
                    else:
                        logger.error(f"No repo_path found for job {job['job_id']} and no workspace_id to construct path")
                        continue
                
                # Create message from complete database job data
                message = {
                    'job_id': job['job_id'],
                    'repo_path': repo_path,
                    'workspace_id': job['workspace_id'],
                    'github_repo': job['github_repo'],
                    'branch': job['github_branch'],
                    'commit_sha': job['commit_sha'],
                    'user_id': job['user_id'],
                    'commit_message': job['commit_message'],
                    'commit_author': job['commit_author'],
                    'metadata': {}
                }
                
                try:
                    # Process the job
                    self.process_message(message)
                    logger.info(f"Successfully processed polled job {job['job_id']}")
                    
                except Exception as e:
                    logger.error(f"Error processing polled job {job['job_id']}: {e}")
                    
                    # Update job status to failed
                    job_repository.update_job_status(
                        job_id=job['job_id'],
                        status="failed",
                        error_message=str(e)
                    )
                    
        except Exception as e:
            logger.error(f"Error polling for jobs: {e}")

    def start(self):
        """Start worker in RabbitMQ or polling mode"""
        logger.info(f"Starting {self.worker_name} worker in {config.job_fetch_mode} mode...")
        
        if config.job_fetch_mode == "polling":
            # Polling mode - check database for cloned jobs
            logger.info("Polling for jobs with status: cloned")
            
            while not self.should_stop:
                self._poll_for_jobs()
                time.sleep(5)  # Poll every 5 seconds
                
        else:
            # RabbitMQ mode - use parent implementation
            super().start()


def main():
    """Main entry point"""
    worker = AIAgentWorker()
    worker.start()


if __name__ == "__main__":
    main()