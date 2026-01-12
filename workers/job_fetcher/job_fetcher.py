import logging
import sys
from typing import Dict, Any

from shared.config import Config
from shared.db import job_repository
from shared.base_worker import JobFetcherBase
from shared.job_logger import JobLogger

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class JobFetcherWorker(JobFetcherBase):
    def __init__(self):
        super().__init__("JobFetcherWorker")

        try:
            self.config = Config()
        except ValueError as e:
            logger.error(f"Configuration error: {e}")
            logger.error("Please check your environment variables and try again.")
            sys.exit(1)

        self.job_repo = job_repository
        self.job_logger = JobLogger("JobFetcher")

    def process_job(self, job: Dict[str, Any]) -> bool:
        """Process a single job by marking it as queued."""
        job_id = job['job_id']
        github_repo = job.get('github_repo', 'unknown')
        commit_sha = job.get('commit_sha', 'unknown')[:8]

        try:
            # Log and update status
            self.job_logger.info(
                job_id, "queued",
                f"Processing new job from {github_repo}@{commit_sha}",
                metadata={"github_repo": github_repo, "commit_sha": commit_sha}
            )

            self.job_repo.update_job_status(job_id, 'queued')

            self.job_logger.info(
                job_id, "queued",
                f"Job queued for cloning",
                progress=100
            )

            logger.info(f"Job {job_id} marked as queued")
            return True

        except Exception as e:
            logger.error(f"Failed to process job {job_id}: {e}")
            self.job_logger.error(job_id, "queued", f"Failed to queue job: {e}", error=e)
            return False


def main() -> None:
    """Main entry point."""
    worker = JobFetcherWorker()

    try:
        worker.run()
    except KeyboardInterrupt:
        worker.shutdown()


if __name__ == "__main__":
    main()
