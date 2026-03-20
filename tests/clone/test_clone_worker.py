"""
Tests for workers/clone/clone_worker.py
"""
import tempfile
import shutil
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from workers.clone.clone_worker import CloneWorker, main


class TestCloneWorker:
    """Test cases for CloneWorker class."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        with patch('workers.clone.clone_worker.config') as mock_config:
            mock_config.shared_storage_path = '/tmp/storage'
            mock_config.ai_agent_enabled = True
            mock_config.database_url = 'postgresql://test'
            yield mock_config

    @pytest.fixture
    def mock_job_repository(self):
        """Create mock job repository."""
        # Need to patch both clone_worker and base_worker's job_repository
        with patch('workers.clone.clone_worker.job_repository') as mock_repo, \
             patch('shared.base_worker.job_repository', mock_repo):
            mock_repo.update_job_status = MagicMock(return_value=True)
            mock_repo.add_job_log = MagicMock(return_value=True)
            yield mock_repo

    @pytest.fixture
    def mock_storage_manager(self):
        """Create mock storage manager."""
        with patch('workers.clone.clone_worker.StorageManager') as MockStorage:
            mock_manager = MagicMock()
            mock_manager.prepare_repository_directory.return_value = Path('/tmp/repos/JOB-123')
            mock_manager.get_storage_info.return_value = {'repositories_count': 5}
            mock_manager.cleanup_repository.return_value = True
            MockStorage.return_value = mock_manager
            yield mock_manager

    @pytest.fixture
    def mock_git_service(self):
        """Create mock git service."""
        with patch('workers.clone.clone_worker.GitService') as MockGit:
            mock_service = MagicMock()
            mock_service.clone_repository = MagicMock()
            mock_service.get_repo_info.return_value = {
                'remote_url': 'https://github.com/user/repo.git',
                'branch': 'main',
                'commit_hash': 'abc123',
                'commit_message': 'Initial commit',
                'commit_author': 'Test User'
            }
            MockGit.return_value = mock_service
            yield mock_service

    @pytest.fixture
    def mock_job_logger(self):
        """Create mock job logger."""
        with patch('workers.clone.clone_worker.JobLogger') as MockLogger:
            mock_logger = MagicMock()
            mock_logger.log_step_start.return_value = 'parent-log-id'
            MockLogger.return_value = mock_logger
            yield mock_logger

    @pytest.fixture
    def clone_worker(self, mock_config, mock_job_repository, mock_storage_manager, mock_git_service, mock_job_logger):
        """Create CloneWorker instance with all mocks."""
        worker = CloneWorker()
        worker.storage_manager = mock_storage_manager
        worker.git_service = mock_git_service
        worker.job_logger = mock_job_logger
        return worker

    @pytest.fixture
    def sample_job(self):
        """Create sample job dictionary."""
        return {
            'job_id': 'JOB-123',
            'github_repo': 'https://github.com/user/repo.git',
            'workspace_id': 'ws-001',
            'github_branch': 'main',
            'commit_sha': 'abc123'
        }

    def test_worker_initialization(self, mock_config, mock_storage_manager, mock_git_service, mock_job_logger):
        """Test CloneWorker initializes correctly."""
        worker = CloneWorker()
        assert worker.worker_name == 'CloneWorker'

    def test_process_job_success(self, clone_worker, sample_job, mock_job_repository, mock_git_service):
        """Test successful job processing."""
        result = clone_worker.process_job(sample_job)

        assert result is True
        mock_git_service.clone_repository.assert_called_once()
        mock_git_service.get_repo_info.assert_called_once()
        mock_job_repository.update_job_status.assert_called()

    def test_process_job_missing_github_repo(self, clone_worker, mock_job_repository):
        """Test handling of missing github_repo."""
        job = {
            'job_id': 'JOB-MISSING',
            'github_repo': None,
            'workspace_id': 'ws-001'
        }

        result = clone_worker.process_job(job)

        assert result is False
        mock_job_repository.update_job_status.assert_called()

    def test_process_job_clone_failure(self, clone_worker, sample_job, mock_git_service, mock_storage_manager):
        """Test handling of clone failure."""
        mock_git_service.clone_repository.side_effect = Exception('Clone failed')

        result = clone_worker.process_job(sample_job)

        assert result is False
        mock_storage_manager.cleanup_repository.assert_called_once()

    def test_process_job_updates_status_to_cloned(self, clone_worker, sample_job, mock_job_repository):
        """Test that status is updated to 'cloned' after successful processing.
        Note: 'cloning' status is now claimed atomically during fetch."""
        clone_worker.process_job(sample_job)

        # 'cloning' is set atomically during fetch; worker sets 'cloned' on success
        calls = mock_job_repository.update_job_status.call_args_list
        statuses = [call[1].get('status') for call in calls if 'status' in call[1]]
        assert 'cloned' in statuses

    def test_process_job_with_branch(self, clone_worker, sample_job, mock_git_service):
        """Test cloning with specific branch."""
        clone_worker.process_job(sample_job)

        call_args = mock_git_service.clone_repository.call_args
        assert call_args[0][2] == 'main'  # branch parameter

    def test_process_job_without_branch(self, clone_worker, sample_job, mock_git_service):
        """Test cloning without specific branch."""
        sample_job['github_branch'] = None

        clone_worker.process_job(sample_job)

        call_args = mock_git_service.clone_repository.call_args
        assert call_args[0][2] is None  # branch parameter

    def test_process_job_sets_cloned_status_ai_enabled(self, clone_worker, sample_job, mock_job_repository, mock_config):
        """Test that status is 'cloned' when AI agent is enabled."""
        mock_config.ai_agent_enabled = True

        clone_worker.process_job(sample_job)

        # Find the final status update
        calls = mock_job_repository.update_job_status.call_args_list
        final_status = None
        for call in calls:
            if 'status' in call[1]:
                final_status = call[1]['status']

        assert final_status == 'cloned'

    def test_process_job_sets_cloned_status_ai_disabled(self, clone_worker, sample_job, mock_job_repository, mock_config):
        """Test that status is 'cloned' even when AI agent is disabled (executor picks up cloned directly)."""
        mock_config.ai_agent_enabled = False

        clone_worker.process_job(sample_job)

        calls = mock_job_repository.update_job_status.call_args_list
        final_status = None
        for call in calls:
            if 'status' in call[1]:
                final_status = call[1]['status']

        assert final_status == 'cloned'

    def test_health_check(self, clone_worker, mock_storage_manager):
        """Test worker health check."""
        # Should not raise
        clone_worker._health_check()

        mock_storage_manager.get_storage_info.assert_called()

    def test_health_check_storage_failure(self, clone_worker, mock_storage_manager):
        """Test health check when storage fails."""
        mock_storage_manager.get_storage_info.side_effect = Exception('Storage error')

        with pytest.raises(Exception, match='Storage error'):
            clone_worker._health_check()

    def test_process_job_saves_repo_metadata(self, clone_worker, sample_job, mock_job_repository, mock_git_service):
        """Test that repository metadata is saved."""
        clone_worker.process_job(sample_job)

        # Find the call that includes repo_metadata
        calls = mock_job_repository.update_job_status.call_args_list
        metadata_saved = False
        for call in calls:
            if 'repo_metadata' in call[1]:
                metadata = call[1]['repo_metadata']
                assert 'remote_url' in metadata or 'cloned_branch' in metadata
                metadata_saved = True

        assert metadata_saved

    def test_process_job_saves_repo_path(self, clone_worker, sample_job, mock_job_repository, mock_storage_manager):
        """Test that repository path is saved."""
        mock_storage_manager.prepare_repository_directory.return_value = Path('/tmp/repos/JOB-123')

        clone_worker.process_job(sample_job)

        calls = mock_job_repository.update_job_status.call_args_list
        path_saved = False
        for call in calls:
            if 'repo_path' in call[1]:
                assert '/tmp/repos/JOB-123' in str(call[1]['repo_path'])
                path_saved = True

        assert path_saved


class TestCloneWorkerIntegration:
    """Integration-style tests for CloneWorker."""

    def test_main_function_exists(self):
        """Test that main function is defined."""
        assert callable(main)
