import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any

sys.path.append('/app')

from shared import BaseWorker, config, job_repository
from shared.job_logger import JobLogger, StepTypes

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ExecuteWorker(BaseWorker):
    """Worker responsible for executing approved jobs in secure environment"""
    
    def __init__(self):
        super().__init__(
            worker_name="ExecuteWorker",
            queue_name=config.execute_queue,
            routing_keys=[config.routing_key_approved]
        )
        
        # Ensure enclave execution output directory exists
        self.output_path = Path(config.shared_storage_path) / "enclave_execution_results"
        self.output_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize job logger
        self.job_logger = JobLogger(self.worker_name)
        
    def _execute_job(self, job_id: str, repo_path: Path) -> Dict[str, Any]:
        """
        Execute the job in a secure environment
        
        TODO: Implement actual execution logic
        - Set up secure execution environment (container/VM)
        - Copy repository to execution environment
        - Install dependencies
        - Execute the code
        - Collect results
        - Clean up environment
        """
        logger.info(f"Executing job {job_id} from {repo_path}")
        
        # Placeholder implementation for secure enclave execution
        result = {
            "execution_type": "secure_enclave",
            "status": "completed",
            "execution_time": "0.0s",
            "output": "Placeholder enclave execution result",
            "artifacts": [],
            "logs": "Execution logs would go here"
        }
        
        # Save result to file
        result_path = self.output_path / job_id / "execution_result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        
        import json
        with open(result_path, 'w') as f:
            json.dump(result, f, indent=2)
            
        result["result_path"] = str(result_path)
        
        return result
        
    def process_message(self, message: Dict[str, Any]):
        """Process an execution job message"""
        job_id = message['job_id']
        repo_path = Path(message['repo_path'])
        workspace_id = message.get('workspace_id')
        ai_decision = message.get('ai_decision', {})
        
        logger.info(f"Processing execution for job {job_id}")
        
        try:
            # Log execution start
            parent_log_id = self.job_logger.log_step_start(
                job_id=job_id,
                step_name="Execute Job",
                step_type=StepTypes.EXECUTE_START,
                message="Starting secure enclave execution",
                metadata={
                    "repo_path": str(repo_path),
                    "workspace_id": workspace_id,
                    "ai_confidence": ai_decision.get('confidence_score'),
                    "ai_reasoning": ai_decision.get('reasoning')
                }
            )
            
            # Update job status to executing
            job_repository.update_job_status(
                job_id=job_id,
                status='executing'
            )
            
            # Log environment preparation
            self.job_logger.log_step_progress(
                job_id=job_id,
                step_name="Prepare Environment",
                step_type=StepTypes.EXECUTE_PREPARING,
                message="Setting up secure execution environment",
                progress=20,
                parent_log_id=parent_log_id
            )
            
            # Keep old log for backward compatibility
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message="Starting job execution",
                metadata={
                    "repo_path": str(repo_path),
                    "ai_confidence": ai_decision.get('confidence_score')
                }
            )
            
            # Log execution running
            self.job_logger.log_step_progress(
                job_id=job_id,
                step_name="Run Code",
                step_type=StepTypes.EXECUTE_RUNNING,
                message="Executing code in secure enclave",
                progress=50,
                parent_log_id=parent_log_id
            )
            
            # Execute the job
            result = self._execute_job(job_id, repo_path)
            
            # Log collecting results
            self.job_logger.log_step_progress(
                job_id=job_id,
                step_name="Collect Results",
                step_type=StepTypes.EXECUTE_COLLECTING,
                message="Collecting execution results and artifacts",
                progress=80,
                parent_log_id=parent_log_id
            )
            
            # Update job status to completed
            job_repository.update_job_status(
                job_id=job_id,
                status='completed',
                execution_result=result,
                execution_result_path=result.get('result_path')
            )
            
            # Log execution completion
            self.job_logger.log_step_complete(
                job_id=job_id,
                step_name="Execution Complete",
                step_type=StepTypes.COMPLETED,
                message="Job execution completed successfully",
                metadata={
                    **result,
                    "workspace_id": workspace_id,
                    "total_duration": "calculated_by_db"
                },
                parent_log_id=parent_log_id
            )
            
            # Keep old log for backward compatibility
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message="Job execution completed successfully",
                metadata=result
            )
            
            # Publish completion message (only in RabbitMQ mode)
            if config.job_fetch_mode != "polling":
                self.publish_message(
                    routing_key=config.routing_key_completed,
                    message={
                        'job_id': job_id,
                        'workspace_id': workspace_id,
                        'result': result,
                        'status': 'completed'
                    }
                )
            
            logger.info(f"Job {job_id} execution completed")
            
        except Exception as e:
            logger.error(f"Execution for job {job_id} failed: {e}")
            
            # Log the error in detailed logs
            self.job_logger.log_step_error(
                job_id=job_id,
                step_name="Execution Failed",
                step_type=StepTypes.FAILED,
                message=f"Execution failed: {str(e)}",
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
                message=f"Execution failed: {str(e)}",
                level="error",
                metadata={
                    "error_type": type(e).__name__,
                    "error_details": str(e)
                }
            )
            
            raise  # Re-raise to let base worker handle the error

    def _poll_for_jobs(self):
        """Poll database for jobs with ai_approved status"""
        try:
            # Get jobs with full data from database including workspace info and repo_path
            with job_repository.db.get_cursor() as cursor:
                query = """
                    SELECT jr.job_id, jr.workspace_id, jr.user_id, jr.commit_sha, 
                           jr.commit_message, jr.commit_author, jr.created_at, jr.status,
                           jr.result_metadata as repo_path, jr.validation_status as ai_confidence_score,
                           jr.validation_decision as ai_reasoning,
                           w.github_repo, w.github_branch
                    FROM job_requests jr
                    JOIN workspaces w ON jr.workspace_id = w.id
                    WHERE jr.status = 'ai_approved'
                    ORDER BY jr.created_at ASC
                    FOR UPDATE OF jr SKIP LOCKED
                """
                cursor.execute(query)
                jobs = cursor.fetchall()
            
            for job in jobs:
                logger.info(f"Found job {job['job_id']} with status {job['status']} for execution")
                
                # Get repo_path from result_metadata field
                repo_path = job.get('repo_path', '')
                if not repo_path:
                    # Fallback: construct repo path from job info
                    repo_path = f"{config.shared_storage_path}/repositories/{job['job_id']}"
                
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
                    'ai_decision': {
                        'approved': True,
                        'confidence_score': job.get('ai_confidence_score', ''),
                        'reasoning': job.get('ai_reasoning', '')
                    }
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
            # Polling mode - check database for ai_approved/ai_rejected jobs
            logger.info("Polling for jobs with status: ai_approved, ai_rejected")
            
            while not self.should_stop:
                self._poll_for_jobs()
                time.sleep(5)  # Poll every 5 seconds
                
        else:
            # RabbitMQ mode - use parent implementation
            super().start()


def main():
    """Main entry point"""
    worker = ExecuteWorker()
    worker.start()


if __name__ == "__main__":
    main()