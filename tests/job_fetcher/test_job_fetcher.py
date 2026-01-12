"""
Tests for workers/job_fetcher/job_fetcher.py
"""
import pytest
from unittest.mock import MagicMock, patch

from workers.job_fetcher.job_fetcher import JobFetcherWorker, main


class TestJobFetcherWorker:
    """Test cases for JobFetcherWorker class."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        with patch('workers.job_fetcher.job_fetcher.Config') as MockConfig:
            mock_config = MagicMock()
            MockConfig.return_value = mock_config
            yield mock_config

    @pytest.fixture
    def mock_job_repository(self):
        """Create mock job repository."""
        with patch('workers.job_fetcher.job_fetcher.job_repository') as mock_repo:
            mock_repo.update_job_status = MagicMock(return_value=True)
            mock_repo.fetch_pending_jobs = MagicMock(return_value=[])
            yield mock_repo

    @pytest.fixture
    def mock_job_logger(self):
        """Create mock job logger."""
        with patch('workers.job_fetcher.job_fetcher.JobLogger') as MockLogger:
            mock_logger = MagicMock()
            MockLogger.return_value = mock_logger
            yield mock_logger

    @pytest.fixture
    def job_fetcher(self, mock_config, mock_job_repository, mock_job_logger):
        """Create JobFetcherWorker instance."""
        worker = JobFetcherWorker()
        return worker

    @pytest.fixture
    def sample_job(self):
        """Create sample pending job."""
        return {
            'job_id': 'JOB-123',
            'github_repo': 'https://github.com/user/repo.git',
            'commit_sha': 'abc123def456',
            'workspace_id': 'ws-001',
            'status': 'pending'
        }

    def test_worker_initialization(self, mock_config, mock_job_repository, mock_job_logger):
        """Test JobFetcherWorker initializes correctly."""
        worker = JobFetcherWorker()

        assert worker.worker_name == 'JobFetcherWorker'

    def test_process_job_success(self, job_fetcher, sample_job, mock_job_repository, mock_job_logger):
        """Test successful job processing."""
        result = job_fetcher.process_job(sample_job)

        assert result is True
        mock_job_repository.update_job_status.assert_called_once_with('JOB-123', 'queued')

    def test_process_job_updates_to_queued(self, job_fetcher, sample_job, mock_job_repository):
        """Test that job status is updated to 'queued'."""
        job_fetcher.process_job(sample_job)

        mock_job_repository.update_job_status.assert_called_with('JOB-123', 'queued')

    def test_process_job_handles_missing_github_repo(self, job_fetcher, mock_job_repository, mock_job_logger):
        """Test handling of job without github_repo."""
        job = {
            'job_id': 'JOB-NO-REPO',
            'commit_sha': 'abc123'
        }

        result = job_fetcher.process_job(job)

        # Should still process successfully (github_repo defaults to 'unknown')
        assert result is True
        mock_job_repository.update_job_status.assert_called_with('JOB-NO-REPO', 'queued')

    def test_process_job_handles_missing_commit_sha(self, job_fetcher, mock_job_repository):
        """Test handling of job without commit_sha."""
        job = {
            'job_id': 'JOB-NO-SHA',
            'github_repo': 'user/repo'
        }

        result = job_fetcher.process_job(job)

        assert result is True
        mock_job_repository.update_job_status.assert_called_with('JOB-NO-SHA', 'queued')

class TestJobFetcherMain:
    """Test cases for main function."""

    def test_main_function_exists(self):
        """Test that main function is defined."""
        assert callable(main)

    def test_main_creates_worker(self, mocker):
        """Test that main creates worker instance."""
        mock_worker = MagicMock()
        mocker.patch('workers.job_fetcher.job_fetcher.JobFetcherWorker', return_value=mock_worker)
        mocker.patch('workers.job_fetcher.job_fetcher.Config')
        mocker.patch('workers.job_fetcher.job_fetcher.job_repository')
        mocker.patch('workers.job_fetcher.job_fetcher.JobLogger')

        # Mock run to raise KeyboardInterrupt to exit
        mock_worker.run.side_effect = KeyboardInterrupt()

        try:
            main()
        except SystemExit:
            pass

        mock_worker.shutdown.assert_called_once()


class TestJobFetcherConfiguration:
    """Test cases for configuration handling."""

    def test_config_error_exits(self, mocker):
        """Test that configuration error causes exit."""
        mocker.patch('workers.job_fetcher.job_fetcher.Config', side_effect=ValueError('Missing DATABASE_URL'))
        mocker.patch('workers.job_fetcher.job_fetcher.job_repository')
        mocker.patch('workers.job_fetcher.job_fetcher.JobLogger')

        with pytest.raises(SystemExit):
            JobFetcherWorker()

