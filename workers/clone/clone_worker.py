import logging
import sys
from typing import Dict, Any

from shared import config
from shared.db import job_repository
from shared.job_logger import JobLogger
from shared.base_worker import CloneWorkerBase
from workers.clone.services import GitService, StorageManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CloneWorker(CloneWorkerBase):
    """Worker responsible for cloning GitHub repositories using pure polling"""

    def __init__(self):
        super().__init__("CloneWorker")

        try:
            self.storage_manager = StorageManager(config.shared_storage_path)
            self.git_service = GitService()
            self.job_logger = JobLogger(self.worker_name)
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            logger.error("Please check your environment variables and try again.")
            sys.exit(1)

    def process_job(self, job: Dict[str, Any]) -> bool:
        """Process a single clone job."""
        job_id = job['job_id']
        github_repo = job.get('github_repo')
        workspace_id = job.get('workspace_id')
        branch = job.get('github_branch')
        commit_sha = job.get('commit_sha')

        logger.info(f"Processing clone job {job_id}")
        logger.info(f"Repository: {github_repo}")
        if branch:
            logger.info(f"Branch: {branch}")
        if commit_sha:
            logger.info(f"Commit SHA: {commit_sha}")

        # Validate required fields
        if not github_repo:
            error_msg = f"Job {job_id}: github_repo is missing or null in database"
            logger.error(error_msg)
            self._update_job_failed(job_id, error_msg)
            return False

        try:
            # Log clone start
            self.job_logger.info(
                job_id, "clone",
                f"Starting clone of repository {github_repo}",
                metadata={
                    "github_repo": github_repo,
                    "branch": branch,
                    "commit_sha": commit_sha,
                    "workspace_id": workspace_id
                }
            )

            # Prepare repository directory
            self.job_logger.info(job_id, "clone", "Preparing repository directory", progress=10)
            repo_path = self.storage_manager.prepare_repository_directory(job_id)

            # Clone the repository
            clone_msg = f"Cloning from {github_repo}" + (f" branch {branch}" if branch else "")
            self.job_logger.info(job_id, "clone", clone_msg, progress=30)
            self.git_service.clone_repository(github_repo, repo_path, branch)

            # Validate and get repo info
            self.job_logger.info(job_id, "clone", "Validating cloned repository", progress=70)
            repo_info = self.git_service.get_repo_info(repo_path)

            # Build metadata
            metadata = {
                **repo_info,
                "storage_info": self.storage_manager.get_storage_info(),
                "original_branch": branch,
                "original_commit_sha": commit_sha,
                "cloned_branch": repo_info.get("branch"),
                "cloned_commit_hash": repo_info.get("commit_hash")
            }

            # Always set to 'cloned' — executor picks up cloned jobs directly
            # AI agent runs independently and writes results as metadata
            next_status = 'cloned'

            # Update job status
            job_repository.update_job_status(
                job_id=job_id,
                status=next_status,
                repo_path=str(repo_path),
                repo_metadata=metadata
            )

            msg = f"Successfully cloned {github_repo}. Ready for execution."

            self.job_logger.info(
                job_id, "clone", msg,
                metadata={
                    "repo_path": str(repo_path),
                    "files_count": metadata.get("files_count", 0),
                    "next_status": next_status
                },
                progress=100
            )

            logger.info(f"Clone job {job_id} completed. Status: {next_status}")
            return True

        except Exception as e:
            logger.error(f"Clone job {job_id} failed: {e}")
            self._handle_job_failure(job_id, e, github_repo, branch)
            return False

    def _handle_job_failure(self, job_id: str, error: Exception, github_repo: str, branch: str) -> None:
        """Handle job failure with cleanup and logging."""
        # Log the error
        self.job_logger.error(
            job_id, "clone",
            f"Clone failed: {error}",
            error=error,
            metadata={"github_repo": github_repo, "branch": branch}
        )

        # Clean up partial clone
        try:
            self.storage_manager.cleanup_repository(job_id)
        except Exception as cleanup_error:
            logger.error(f"Cleanup failed: {cleanup_error}")
            self.job_logger.warning(
                job_id, "clone",
                f"Failed to clean up repository: {cleanup_error}"
            )

        # Update job status to failed
        self._update_job_failed(job_id, str(error))

    def _health_check(self) -> None:
        """Perform worker-specific health checks."""
        super()._health_check()

        try:
            self.storage_manager.get_storage_info()
        except Exception as e:
            logger.error(f"Storage manager health check failed: {e}")
            raise


def main() -> None:
    """Main entry point."""
    worker = CloneWorker()

    try:
        worker.run()
    except KeyboardInterrupt:
        worker.shutdown()


if __name__ == "__main__":
    main()
