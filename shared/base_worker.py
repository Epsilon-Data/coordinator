import logging
import os
import signal
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, List

from .db import job_repository
from .job_logger import JobLogger


class BaseWorker(ABC):
    """
    Abstract base class for all polling-based workers.

    Subclasses must implement:
        - fetch_jobs(): Return list of jobs to process
        - process_job(job): Process a single job, return True on success

    Provides:
        - Signal handling (SIGINT/SIGTERM) for graceful shutdown
        - Main polling loop with configurable intervals (POLLING_INTERVAL env)
        - Health checks before starting
        - Error handling and job failure updates
        - Statistics tracking (jobs processed, success rate, uptime)

    Usage:
        class MyWorker(BaseWorker):
            def fetch_jobs(self):
                return db.get_pending_jobs()

            def process_job(self, job):
                # Process job...
                return True

        worker = MyWorker("MyWorker")
        worker.run()  # Starts polling loop
    """

    def __init__(self, worker_name: str):
        """
        Initialize base worker with common configuration.

        Args:
            worker_name: Name of the worker for logging purposes
        """
        self.worker_name = worker_name
        self.polling_interval = int(os.getenv('POLLING_INTERVAL', '5'))
        self.batch_size = int(os.getenv('BATCH_SIZE', '5'))
        self.is_running = False

        # Initialize loggers
        if "executor" in worker_name.lower():
            self.logger = logging.getLogger("epsilon.executor")
        else:
            self.logger = logging.getLogger(f"{worker_name.lower()}")

        self._job_logger = JobLogger(worker_name)

        # Statistics
        self.stats = {
            'jobs_processed': 0,
            'jobs_successful': 0,
            'jobs_failed': 0,
            'start_time': None,
            'total_cycles': 0
        }

        self._setup_signal_handlers()
        self.logger.info(f"{self.worker_name} initialized with polling interval: {self.polling_interval}s, batch size: {self.batch_size}")

    def _setup_signal_handlers(self) -> None:
        """Setup graceful shutdown signal handlers."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum: int, _) -> None:
        """Handle shutdown signals gracefully."""
        self.logger.info(f"{self.worker_name} received signal {signum}, initiating graceful shutdown...")
        self.is_running = False

    @abstractmethod
    def fetch_jobs(self) -> List[Dict[str, Any]]:
        """
        Fetch jobs for processing from the database.

        Returns:
            List of job dictionaries ready for processing
        """
        pass

    @abstractmethod
    def process_job(self, job: Dict[str, Any]) -> bool:
        """
        Process a single job.

        Args:
            job: Job dictionary from database

        Returns:
            True if successful, False if failed
        """
        pass

    def _update_job_failed(self, job_id: str, error_message: str) -> None:
        """Update job status to failed with error message."""
        try:
            job_repository.update_job_status(
                job_id=job_id,
                status="failed",
                error_message=error_message
            )
            self._job_logger.error(job_id, "failed", f"Job failed: {error_message}")
            self.logger.error(f"{self.worker_name}: Job {job_id} marked as failed - {error_message}")
        except Exception as e:
            self.logger.error(f"{self.worker_name}: Failed to update job {job_id} status: {e}")

    def _poll_cycle(self) -> int:
        """
        Execute a single polling cycle.

        Returns:
            Number of jobs processed successfully in this cycle
        """
        try:
            self.logger.info(f"{self.worker_name}: Starting polling cycle...")
            jobs = self.fetch_jobs()
            self.logger.info(f"{self.worker_name}: Fetch completed, got {len(jobs) if jobs else 0} jobs")

            if not jobs:
                self.logger.info(f"{self.worker_name}: No jobs found")
                return 0

            processed_count = 0
            self.stats['total_cycles'] += 1

            for job in jobs:
                job_id = job.get('job_id', 'unknown')

                try:
                    self.logger.info(f"{self.worker_name}: Processing job {job_id}")

                    success = self.process_job(job)
                    self.stats['jobs_processed'] += 1

                    if success:
                        processed_count += 1
                        self.stats['jobs_successful'] += 1
                        self.logger.info(f"{self.worker_name}: Successfully processed job {job_id}")
                    else:
                        self.stats['jobs_failed'] += 1
                        self.logger.error(f"{self.worker_name}: Failed to process job {job_id}")

                except Exception as e:
                    self.stats['jobs_processed'] += 1
                    self.stats['jobs_failed'] += 1
                    self.logger.error(f"{self.worker_name}: Unexpected error processing job {job_id}: {e}")
                    self._update_job_failed(job_id, str(e))

            if processed_count > 0:
                self.logger.info(f"{self.worker_name}: Polling cycle completed - {processed_count}/{len(jobs)} jobs processed successfully")

            return processed_count

        except Exception as e:
            self.logger.error(f"{self.worker_name}: Error during polling cycle: {e}")
            return 0

    def _health_check(self) -> None:
        """
        Perform basic health checks.
        Override this method to add worker-specific health checks.
        """
        try:
            # Test database connectivity by trying to fetch jobs
            self.fetch_jobs()
            self.logger.debug(f"{self.worker_name}: Health check passed")
        except Exception as e:
            self.logger.error(f"{self.worker_name}: Health check failed: {e}")
            raise

    def _log_stats(self) -> None:
        """Log worker statistics."""
        if self.stats['start_time']:
            uptime = time.time() - self.stats['start_time']
            success_rate = (self.stats['jobs_successful'] / max(1, self.stats['jobs_processed'])) * 100

            self.logger.info(f"{self.worker_name} Statistics:")
            self.logger.info(f"  Uptime: {uptime:.1f}s")
            self.logger.info(f"  Jobs Processed: {self.stats['jobs_processed']}")
            self.logger.info(f"  Jobs Successful: {self.stats['jobs_successful']}")
            self.logger.info(f"  Jobs Failed: {self.stats['jobs_failed']}")
            self.logger.info(f"  Success Rate: {success_rate:.1f}%")
            self.logger.info(f"  Polling Cycles: {self.stats['total_cycles']}")

    def run(self) -> None:
        """Main worker loop with graceful shutdown support."""
        self.logger.info(f"Starting {self.worker_name} (Pure Polling Mode)")
        self.logger.info(f"Configuration - Polling interval: {self.polling_interval}s, Batch size: {self.batch_size}")

        try:
            self._health_check()
            self.logger.info(f"{self.worker_name}: Initial health check passed")
        except Exception as e:
            self.logger.error(f"{self.worker_name}: Failed initial health check: {e}")
            return

        self.is_running = True
        self.stats['start_time'] = time.time()

        while self.is_running:
            start_time = time.time()

            try:
                self._poll_cycle()
                # Calculate sleep time accounting for processing duration
                processing_time = time.time() - start_time
                sleep_time = max(0.0, self.polling_interval - processing_time)

                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    self.logger.warning(f"{self.worker_name}: Polling cycle took {processing_time:.2f}s, longer than interval {self.polling_interval}s")

            except KeyboardInterrupt:
                self.logger.info(f"{self.worker_name}: Received interrupt signal")
                break
            except Exception as e:
                self.logger.error(f"{self.worker_name}: Unexpected error in main loop: {e}")
                time.sleep(self.polling_interval)

        self._log_stats()
        self.logger.info(f"{self.worker_name} stopped gracefully")

    def shutdown(self) -> None:
        """Gracefully shutdown the worker."""
        self.logger.info(f"{self.worker_name}: Initiating graceful shutdown...")
        self.is_running = False


class JobFetcherBase(BaseWorker):
    """Base class specifically for job fetcher type workers."""

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        """Fetch pending jobs for processing."""
        return job_repository.fetch_pending_jobs(self.batch_size)


class CloneWorkerBase(BaseWorker):
    """Base class specifically for clone type workers."""

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        """Fetch queued jobs for cloning."""
        return job_repository.fetch_queued_jobs(self.batch_size)


class AIWorkerBase(BaseWorker):
    """Base class specifically for AI analysis workers."""

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        """Fetch cloned jobs for AI analysis."""
        return job_repository.fetch_cloned_jobs(self.batch_size)


class ExecutorWorkerBase(BaseWorker):
    """Base class specifically for execution workers."""

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        """Fetch AI approved jobs for execution."""
        return job_repository.fetch_ai_approved_jobs(self.batch_size)


