"""
Executor worker implementing high-level logic:
fetch job -> load repo -> get public_key -> zip & encrypt -> send to enclave
"""
import os
import sys
from typing import Dict, Any, Optional

from shared.db import job_repository
from shared.base_worker import ExecutorWorkerBase
from workers.executor.utils import get_logger, setup_logging
from workers.executor.interfaces import IExecutor
from workers.executor.models import JobExecutionRequest, JobStatus
from workers.executor.settings import get_settings, validate_and_raise
from workers.executor.exceptions import ConfigurationError
from workers.executor.factories import ExecutorFactory


class ExecutorWorker(ExecutorWorkerBase):
    """Database polling worker for processing job execution requests."""

    def __init__(self, executor: Optional[IExecutor] = None):
        """
        Initialize executor worker.

        Args:
            executor: Optional job executor instance (if None, will create one)
        """
        super().__init__("ExecutorWorker")

        self._settings = get_settings()

        # Validate environment
        self._validate_environment()

        # Create executor if not provided
        if executor is None:
            executor = self._create_executor()

        self._executor = executor

        # Check if executor is ready
        if not self._executor.is_ready:
            raise RuntimeError("Executor is not ready to accept jobs")

        logger = get_logger(__name__)
        logger.info(f"ExecutorWorker initialized for worker {self._settings.worker_id} in polling mode")

    def _validate_environment(self) -> None:
        """Validate the runtime environment configuration."""
        logger = get_logger(__name__)
        logger.info("Validating environment configuration...")

        try:
            validate_and_raise(self._settings)
            logger.info("[SUCCESS] Environment validation passed")

            # Log key configuration
            logger.info(f"Worker ID: {self._settings.worker_id}")
            logger.info(f"Environment: {self._settings.environment}")
            logger.info(f"Use local enclave: {self._settings.enclave.use_local_client}")
            logger.info(f"Storage path: {self._settings.storage.shared_storage_path}")

        except Exception as e:
            logger.error(f"[ERROR] Configuration validation failed: {e}")
            raise ConfigurationError(f"Configuration validation failed: {e}")

    def _create_executor(self) -> IExecutor:
        """Create executor instance."""
        logger = get_logger(__name__)
        logger.info("Creating production executor")
        return ExecutorFactory.create_executor(self._settings)

    def process_job(self, job: Dict[str, Any]) -> bool:
        """
        High-level logic: fetch job -> load repo -> get public_key -> zip & encrypt -> send to enclave
        """
        job_id = job['job_id']
        logger = get_logger(__name__)

        try:
            logger.info(f"[JOB] ========== PROCESSING JOB {job_id} ==========")
            logger.info(f"[JOB] Job details: {job}")
            logger.info(f"[JOB] Starting job processing pipeline...")

            # Mark job as executing
            job_repository.update_job_status(job_id=job_id, status='executing')

            # Step 1: Load cloned repository
            repo_path = f"{self._settings.storage.shared_storage_path}/repositories/{job_id}"
            if not os.path.exists(repo_path):
                raise Exception(f"Repository not found: {repo_path}")

            logger.info(f"[REPO] Found repository at {repo_path}")

            # Step 2: Create job request
            job_request = JobExecutionRequest(
                job_id=job_id,
                repo_path=repo_path,
                script_path='example_analysis.py',
                data_path=None,
                workspace_id=job['workspace_id'],
                ai_decision={
                    'commit_sha': job['commit_sha'],
                    'github_repo': job['github_repo'],
                    'github_branch': job['github_branch']
                },
                metadata={
                    'user_id': job['user_id'],
                    'commit_message': job['commit_message'],
                    'commit_author': job['commit_author'],
                    'created_at': str(job['created_at'])
                }
            )

            # Step 3: Execute using high-level logic (get public_key, zip, encrypt, send to enclave)
            result = self._executor.execute(job_request)

            # Update job status based on result
            if result.is_success:
                job_repository.update_job_status(
                    job_id=job_id,
                    status=JobStatus.SUCCESS.value,
                    execution_result=result.output
                )
                logger.info(f"[SUCCESS] Job {job_id} completed")
                return True
            elif result.status == JobStatus.REJECTED:
                # Build validation failed - no build folder or invalid build.yml
                job_repository.update_job_status(
                    job_id=job_id,
                    status=JobStatus.REJECTED.value,
                    error_message=result.error
                )
                logger.warning(f"[REJECTED] Job {job_id} rejected: {result.error}")
                return False
            else:
                job_repository.update_job_status(
                    job_id=job_id,
                    status=JobStatus.FAILED.value,
                    error_message=result.error
                )
                logger.error(f"[FAILED] Job {job_id} failed: {result.error}")
                return False

        except Exception as e:
            logger.error(f"[ERROR] Job {job_id} failed: {str(e)}")
            job_repository.update_job_status(job_id=job_id, status=JobStatus.FAILED.value, output=str(e))
            return False


def main() -> None:
    """Main entry point."""
    # Set up logging
    logger = setup_logging(
        name='epsilon.executor',
        level=os.getenv('LOG_LEVEL', 'INFO')
    )

    logger.info("[START] Starting Epsilon Executor Worker")
    logger.info("=" * 60)

    try:
        # Create and start worker
        worker = ExecutorWorker()
        logger.info("[WORKER] Starting worker loop...")
        worker.run()

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("[WORKER] Executor worker stopped")


if __name__ == "__main__":
    main()
