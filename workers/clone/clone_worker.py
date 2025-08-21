import logging
import sys
from typing import Dict, Any

# Add paths for imports
sys.path.append('/app')

from shared import BaseWorker, config, job_repository
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

    def process_message(self, message: Dict[str, Any]):
        """Process a clone job message"""
        job_id = message['job_id']
        github_repo = message['github_repo']
        workspace_id = message.get('workspace_id')
        branch = message.get('branch')  # Branch name from DB
        commit_sha = message.get('commit_sha')  # Commit SHA from DB
        
        logger.info(f"Processing clone job {job_id}")
        logger.info(f"Repository: {github_repo}")
        if branch:
            logger.info(f"Branch: {branch}")
        if commit_sha:
            logger.info(f"Commit SHA: {commit_sha}")
        
        try:
            # Update job status to cloning
            job_repository.update_job_status(
                job_id=job_id,
                status='cloning'
            )
            
            # Prepare repository directory
            repo_path = self.storage_manager.prepare_repository_directory(job_id)
            
            # Clone the repository with specific branch if provided
            self.git_service.clone_repository(github_repo, repo_path, branch)
            
            # Get repository info from the cloned repo
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
            
            # Log success
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
            
            # Publish message for next stage
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
            
            # Clean up any partial clone
            try:
                self.storage_manager.cleanup_repository(job_id)
            except Exception as cleanup_error:
                logger.error(f"Cleanup failed: {cleanup_error}")
                
            raise  # Re-raise to let base worker handle the error


def main():
    """Main entry point"""
    worker = CloneWorker()
    worker.start()


if __name__ == "__main__":
    main()