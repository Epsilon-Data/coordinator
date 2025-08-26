import logging
import sys
import time
from typing import Dict, Any

# Add paths for imports
sys.path.append('/app')

from shared import BaseWorker, config, job_repository
from shared.job_logger import JobLogger, StepTypes
from services import GitService
from utils import StorageManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CloneWorker(BaseWorker):
    """Worker responsible for cloning GitHub repositories"""
    
    def __init__(self):
        super().__init__(
            worker_name="CloneWorker",
            queue_name=config.clone_queue,
            routing_keys=[config.routing_key_created]
        )
        
        # Initialize services
        self.storage_manager = StorageManager(config.shared_storage_path)
        self.git_service = GitService()
        self.job_logger = JobLogger(self.worker_name)

    def process_message(self, message: Dict[str, Any]):
        """Process a clone job message"""
        job_id = message['job_id']
        github_repo = message.get('github_repo')
        workspace_id = message.get('workspace_id')
        branch = message.get('branch')  # Branch name from DB
        commit_sha = message.get('commit_sha')  # Commit SHA from DB
        
        logger.info(f"Processing clone job {job_id}")
        logger.info(f"Repository: {github_repo}")
        if branch:
            logger.info(f"Branch: {branch}")
        if commit_sha:
            logger.info(f"Commit SHA: {commit_sha}")
            
        # Validate required fields
        if not github_repo:
            raise ValueError(f"Job {job_id}: github_repo is missing or null in database")
        
        try:
            # Log clone start
            parent_log_id = self.job_logger.log_step_start(
                job_id=job_id,
                step_name="Clone Repository",
                step_type=StepTypes.CLONE_START,
                message=f"Starting clone of repository {github_repo}",
                metadata={
                    "github_repo": github_repo,
                    "branch": branch,
                    "commit_sha": commit_sha,
                    "workspace_id": workspace_id
                }
            )
            
            # Update job status to cloning
            job_repository.update_job_status(
                job_id=job_id,
                status='cloning'
            )
            
            # Prepare repository directory
            self.job_logger.log_step_progress(
                job_id=job_id,
                step_name="Prepare Directory",
                step_type=StepTypes.CLONE_PREPARING,
                message="Preparing repository directory",
                progress=10,
                parent_log_id=parent_log_id
            )
            repo_path = self.storage_manager.prepare_repository_directory(job_id)
            
            # Clone the repository with specific branch if provided
            self.job_logger.log_step_progress(
                job_id=job_id,
                step_name="Git Clone",
                step_type=StepTypes.CLONE_DOWNLOADING,
                message=f"Cloning from {github_repo}" + (f" branch {branch}" if branch else ""),
                progress=30,
                metadata={"target_path": str(repo_path)},
                parent_log_id=parent_log_id
            )
            self.git_service.clone_repository(github_repo, repo_path, branch)
            
            # Get repository info from the cloned repo
            self.job_logger.log_step_progress(
                job_id=job_id,
                step_name="Validate Repository",
                step_type=StepTypes.CLONE_VALIDATING,
                message="Validating cloned repository and gathering metadata",
                progress=70,
                parent_log_id=parent_log_id
            )
            repo_info = self.git_service.get_repo_info(repo_path)
            
            # Combine metadata with info from message
            metadata = {
                **repo_info,
                "storage_info": self.storage_manager.get_storage_info(),
                "original_branch": branch,  # Branch from message
                "original_commit_sha": commit_sha,  # Commit SHA from message
                "cloned_branch": repo_info.get("branch"),  # Actual cloned branch
                "cloned_commit_hash": repo_info.get("commit_hash")  # Actual commit hash
            }
            
            # Update job status to cloned
            job_repository.update_job_status(
                job_id=job_id,
                status='cloned',
                repo_path=str(repo_path),
                repo_metadata=metadata
            )
            
            # Log clone completion
            self.job_logger.log_step_complete(
                job_id=job_id,
                step_name="Clone Complete",
                step_type=StepTypes.CLONED,
                message=f"Successfully cloned {github_repo} to {repo_path}",
                metadata={
                    "repo_url": github_repo,
                    "branch": branch,
                    "commit_sha": commit_sha,
                    "repo_path": str(repo_path),
                    "files_count": metadata.get("files_count", 0),
                    "main_language": metadata.get("main_language"),
                    "size_mb": metadata.get("total_size_mb", 0),
                    "languages": metadata.get("languages", {})
                },
                parent_log_id=parent_log_id
            )
            
            # Keep the old log for backward compatibility
            job_repository.add_job_log(
                job_id=job_id,
                worker_name=self.worker_name,
                message=f"Repository cloned and validated successfully",
                metadata={
                    "repo_url": github_repo,
                    "branch": branch,
                    "commit_sha": commit_sha,
                    "repo_path": str(repo_path),
                    "files_count": metadata.get("files_count", 0),
                    "main_language": metadata.get("main_language"),
                    "size_mb": metadata.get("total_size_mb", 0)
                }
            )
            
            # Publish message for next stage (only in RabbitMQ mode)
            if config.job_fetch_mode != "polling":
                self.publish_message(
                    routing_key=config.routing_key_cloned,
                    message={
                        'job_id': job_id,
                        'repo_path': str(repo_path),
                        'workspace_id': workspace_id,
                        'metadata': metadata,
                        'branch': branch,
                        'commit_sha': commit_sha
                    }
                )
            
            logger.info(f"Clone job {job_id} completed successfully")
            
        except Exception as e:
            logger.error(f"Clone job {job_id} failed: {e}")
            
            # Log the error in detailed logs
            self.job_logger.log_step_error(
                job_id=job_id,
                step_name="Clone Failed",
                step_type=StepTypes.FAILED,
                message=f"Clone failed: {str(e)}",
                error=e,
                metadata={
                    "github_repo": github_repo,
                    "branch": branch,
                    "error_type": type(e).__name__
                },
                parent_log_id=parent_log_id if 'parent_log_id' in locals() else None
            )
            
            # Clean up any partial clone
            try:
                self.storage_manager.cleanup_repository(job_id)
            except Exception as cleanup_error:
                logger.error(f"Cleanup failed: {cleanup_error}")
                self.job_logger.log_warning(
                    job_id=job_id,
                    step_name="Cleanup Failed",
                    step_type=StepTypes.ERROR,
                    message=f"Failed to clean up repository: {cleanup_error}",
                    metadata={"cleanup_error": str(cleanup_error)}
                )
                
            raise  # Re-raise to let base worker handle the error


    def _poll_for_jobs(self):
        """Poll database for jobs with queued status"""
        try:
            # Get jobs with full data from database including workspace info
            with job_repository.db.get_cursor() as cursor:
                query = """
                    SELECT jr.job_id, jr.workspace_id, jr.user_id, jr.commit_sha, 
                           jr.commit_message, jr.commit_author, jr.created_at, jr.status,
                           w.github_repo, w.github_branch
                    FROM job_requests jr
                    JOIN workspaces w ON jr.workspace_id = w.id
                    WHERE jr.status = 'queued'
                    ORDER BY jr.created_at ASC
                    FOR UPDATE OF jr SKIP LOCKED
                """
                cursor.execute(query)
                jobs = cursor.fetchall()
            
            for job in jobs:
                logger.info(f"Found queued job {job['job_id']} for clone processing")
                logger.info(f"DEBUG: Job data: {dict(job)}")  # Debug log to see all fields
                
                # Create message from complete database job data
                message = {
                    'job_id': job['job_id'],
                    'github_repo': job.get('github_repo'),
                    'workspace_id': job.get('workspace_id'),
                    'branch': job.get('github_branch'),
                    'commit_sha': job.get('commit_sha'),
                    'user_id': job.get('user_id'),
                    'commit_message': job.get('commit_message'),
                    'commit_author': job.get('commit_author')
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
            # Polling mode - check database for queued jobs
            logger.info("Polling for jobs with status: queued")
            
            while not self.should_stop:
                self._poll_for_jobs()
                time.sleep(5)  # Poll every 5 seconds
                
        else:
            # RabbitMQ mode - use parent implementation
            super().start()


def main():
    """Main entry point"""
    worker = CloneWorker()
    worker.start()


if __name__ == "__main__":
    main()