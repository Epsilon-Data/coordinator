"""
Tests for shared/base_worker.py
"""
import os
import signal
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import time

from shared.base_worker import BaseWorker, JobFetcherBase, CloneWorkerBase, AIWorkerBase, ExecutorWorkerBase


class TestBaseWorker:
    """Test cases for BaseWorker abstract class."""

    @pytest.fixture
    def mock_job_repository(self):
        """Mock job_repository for tests."""
        with patch('shared.base_worker.job_repository') as mock:
            mock.update_job_status = MagicMock(return_value=True)
            mock.add_job_log = MagicMock(return_value=True)
            mock.fetch_pending_jobs = MagicMock(return_value=[])
            mock.fetch_queued_jobs = MagicMock(return_value=[])
            mock.fetch_cloned_jobs = MagicMock(return_value=[])
            mock.fetch_ai_approved_jobs = MagicMock(return_value=[])
            yield mock

    @pytest.fixture
    def concrete_worker(self, mock_job_repository):
        """Create a concrete implementation of BaseWorker for testing."""
        class TestWorker(BaseWorker):
            def __init__(self):
                super().__init__('TestWorker')
                self.processed_jobs = []

            def fetch_jobs(self):
                return mock_job_repository.fetch_pending_jobs(self.batch_size)

            def process_job(self, job):
                self.processed_jobs.append(job)
                return True

        return TestWorker()

    def test_worker_initialization(self, concrete_worker):
        """Test BaseWorker initializes correctly."""
        assert concrete_worker.worker_name == 'TestWorker'
        assert concrete_worker.polling_interval == 5  # default
        assert concrete_worker.batch_size == 5  # default
        assert concrete_worker.is_running is False
        assert concrete_worker.stats['jobs_processed'] == 0

    def test_worker_custom_polling_interval(self, mock_job_repository):
        """Test worker uses custom polling interval from environment."""
        os.environ['POLLING_INTERVAL'] = '10'
        os.environ['BATCH_SIZE'] = '20'

        class CustomWorker(BaseWorker):
            def fetch_jobs(self):
                return []

            def process_job(self, job):
                return True

        worker = CustomWorker('CustomWorker')

        assert worker.polling_interval == 10
        assert worker.batch_size == 20

        # Cleanup
        os.environ.pop('POLLING_INTERVAL', None)
        os.environ.pop('BATCH_SIZE', None)

    def test_signal_handler_stops_worker(self, concrete_worker):
        """Test signal handler sets is_running to False."""
        concrete_worker.is_running = True
        concrete_worker._signal_handler(signal.SIGTERM, None)
        assert concrete_worker.is_running is False

    def test_poll_cycle_processes_jobs(self, concrete_worker, mock_job_repository):
        """Test _poll_cycle processes fetched jobs."""
        test_jobs = [
            {'job_id': 'JOB-1'},
            {'job_id': 'JOB-2'},
        ]
        mock_job_repository.fetch_pending_jobs.return_value = test_jobs

        processed = concrete_worker._poll_cycle()

        assert processed == 2
        assert len(concrete_worker.processed_jobs) == 2
        assert concrete_worker.stats['jobs_processed'] == 2
        assert concrete_worker.stats['jobs_successful'] == 2

    def test_poll_cycle_handles_no_jobs(self, concrete_worker, mock_job_repository):
        """Test _poll_cycle handles empty job list."""
        mock_job_repository.fetch_pending_jobs.return_value = []

        processed = concrete_worker._poll_cycle()

        assert processed == 0
        assert len(concrete_worker.processed_jobs) == 0

    def test_poll_cycle_handles_job_failure(self, mock_job_repository):
        """Test _poll_cycle handles job processing failure."""
        class FailingWorker(BaseWorker):
            def fetch_jobs(self):
                return [{'job_id': 'JOB-FAIL'}]

            def process_job(self, job):
                return False  # Simulate failure

        worker = FailingWorker('FailingWorker')
        processed = worker._poll_cycle()

        assert processed == 0
        assert worker.stats['jobs_failed'] == 1

    def test_poll_cycle_handles_exception(self, mock_job_repository):
        """Test _poll_cycle handles exception during processing."""
        class ExceptionWorker(BaseWorker):
            def fetch_jobs(self):
                return [{'job_id': 'JOB-EXC'}]

            def process_job(self, job):
                raise RuntimeError("Processing failed")

        worker = ExceptionWorker('ExceptionWorker')
        processed = worker._poll_cycle()

        assert processed == 0
        assert worker.stats['jobs_failed'] == 1
        mock_job_repository.update_job_status.assert_called_with(
            job_id='JOB-EXC',
            status='failed',
            error_message='Processing failed'
        )

    def test_health_check_calls_fetch_jobs(self, concrete_worker, mock_job_repository):
        """Test _health_check attempts to fetch jobs."""
        concrete_worker._health_check()
        mock_job_repository.fetch_pending_jobs.assert_called()

    def test_health_check_raises_on_failure(self, mock_job_repository):
        """Test _health_check raises exception on failure."""
        class HealthCheckWorker(BaseWorker):
            def fetch_jobs(self):
                raise ConnectionError("Database unavailable")

            def process_job(self, job):
                return True

        worker = HealthCheckWorker('HealthCheckWorker')

        with pytest.raises(ConnectionError, match="Database unavailable"):
            worker._health_check()

    def test_log_stats(self, concrete_worker, capsys):
        """Test _log_stats outputs statistics."""
        concrete_worker.stats['start_time'] = time.time()
        concrete_worker.stats['jobs_processed'] = 10
        concrete_worker.stats['jobs_successful'] = 8
        concrete_worker.stats['jobs_failed'] = 2
        concrete_worker.stats['total_cycles'] = 5

        concrete_worker._log_stats()

        # Statistics should be logged (captured by logger, not stdout)
        # Just verify it doesn't raise

    def test_shutdown_stops_worker(self, concrete_worker):
        """Test shutdown() stops the worker."""
        concrete_worker.is_running = True
        concrete_worker.shutdown()
        assert concrete_worker.is_running is False


class TestJobFetcherBase:
    """Test cases for JobFetcherBase class."""

    @pytest.fixture
    def job_fetcher(self):
        """Create JobFetcherBase instance."""
        with patch('shared.base_worker.job_repository') as mock:
            mock.fetch_pending_jobs = MagicMock(return_value=[])

            class TestJobFetcher(JobFetcherBase):
                def process_job(self, job):
                    return True

            yield TestJobFetcher('TestJobFetcher'), mock

    def test_fetch_jobs_calls_pending(self, job_fetcher):
        """Test fetch_jobs calls fetch_pending_jobs."""
        worker, mock_repo = job_fetcher
        worker.fetch_jobs()
        mock_repo.fetch_pending_jobs.assert_called_once_with(worker.batch_size)


class TestCloneWorkerBase:
    """Test cases for CloneWorkerBase class."""

    @pytest.fixture
    def clone_worker(self):
        """Create CloneWorkerBase instance."""
        with patch('shared.base_worker.job_repository') as mock:
            mock.fetch_queued_jobs = MagicMock(return_value=[])

            class TestCloneWorker(CloneWorkerBase):
                def process_job(self, job):
                    return True

            yield TestCloneWorker('TestCloneWorker'), mock

    def test_fetch_jobs_calls_queued(self, clone_worker):
        """Test fetch_jobs calls fetch_queued_jobs."""
        worker, mock_repo = clone_worker
        worker.fetch_jobs()
        mock_repo.fetch_queued_jobs.assert_called_once_with(worker.batch_size)


class TestAIWorkerBase:
    """Test cases for AIWorkerBase class."""

    @pytest.fixture
    def ai_worker(self):
        """Create AIWorkerBase instance."""
        with patch('shared.base_worker.job_repository') as mock:
            mock.fetch_cloned_jobs = MagicMock(return_value=[])

            class TestAIWorker(AIWorkerBase):
                def process_job(self, job):
                    return True

            yield TestAIWorker('TestAIWorker'), mock

    def test_fetch_jobs_calls_cloned(self, ai_worker):
        """Test fetch_jobs calls fetch_cloned_jobs."""
        worker, mock_repo = ai_worker
        worker.fetch_jobs()
        mock_repo.fetch_cloned_jobs.assert_called_once_with(worker.batch_size)


class TestExecutorWorkerBase:
    """Test cases for ExecutorWorkerBase class."""

    @pytest.fixture
    def executor_worker(self):
        """Create ExecutorWorkerBase instance."""
        with patch('shared.base_worker.job_repository') as mock:
            mock.fetch_ai_approved_jobs = MagicMock(return_value=[])

            class TestExecutorWorker(ExecutorWorkerBase):
                def process_job(self, job):
                    return True

            yield TestExecutorWorker('TestExecutorWorker'), mock

    def test_fetch_jobs_calls_ai_approved(self, executor_worker):
        """Test fetch_jobs calls fetch_ai_approved_jobs."""
        worker, mock_repo = executor_worker
        worker.fetch_jobs()
        mock_repo.fetch_ai_approved_jobs.assert_called_once_with(worker.batch_size)

    def test_executor_logger_name(self, executor_worker):
        """Test ExecutorWorkerBase uses correct logger name."""
        worker, _ = executor_worker
        # Executor workers should use epsilon.executor logger
        assert 'executor' in worker.logger.name.lower()
