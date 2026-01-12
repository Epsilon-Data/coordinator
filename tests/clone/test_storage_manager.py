"""
Tests for workers/clone/services.py - StorageManager
"""
import tempfile
import shutil
from pathlib import Path
import pytest

from workers.clone.services import StorageManager


class TestStorageManager:
    """Test cases for StorageManager class."""

    @pytest.fixture
    def temp_base_path(self):
        """Create temporary base path."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def storage_manager(self, temp_base_path):
        """Create StorageManager instance."""
        return StorageManager(temp_base_path)

    def test_initialization(self, temp_base_path):
        """Test StorageManager initializes correctly."""
        manager = StorageManager(temp_base_path)

        assert manager.base_path == Path(temp_base_path)
        assert manager.repositories_path == Path(temp_base_path) / 'repositories'

    def test_initialization_creates_directories(self, temp_base_path):
        """Test that initialization creates required directories."""
        manager = StorageManager(temp_base_path)

        assert manager.base_path.exists()
        assert manager.repositories_path.exists()
        assert (manager.base_path / 'ai_analysis').exists()
        assert (manager.base_path / 'execution_results').exists()

    def test_get_repository_path(self, storage_manager):
        """Test getting repository path."""
        repo_path = storage_manager.get_repository_path('JOB-123')

        expected = storage_manager.repositories_path / 'JOB-123'
        assert repo_path == expected

    def test_prepare_repository_directory(self, storage_manager):
        """Test preparing repository directory."""
        repo_path = storage_manager.prepare_repository_directory('JOB-123')

        assert repo_path.parent.exists()
        assert repo_path == storage_manager.repositories_path / 'JOB-123'

    def test_prepare_repository_directory_removes_existing(self, storage_manager):
        """Test that existing directory is removed."""
        job_id = 'JOB-EXISTING'

        # Create existing directory with content
        existing_path = storage_manager.get_repository_path(job_id)
        existing_path.mkdir(parents=True)
        (existing_path / 'old_file.txt').write_text('old content')

        # Prepare should remove existing
        repo_path = storage_manager.prepare_repository_directory(job_id)

        assert repo_path == existing_path
        # Old content should be gone (directory removed and not recreated by prepare)
        assert not existing_path.exists()

    def test_cleanup_repository_success(self, storage_manager):
        """Test successful repository cleanup."""
        job_id = 'JOB-CLEANUP'

        # Create repository with content
        repo_path = storage_manager.get_repository_path(job_id)
        repo_path.mkdir(parents=True)
        (repo_path / 'file.txt').write_text('content')

        result = storage_manager.cleanup_repository(job_id)

        assert result is True
        assert not repo_path.exists()

    def test_cleanup_repository_not_found(self, storage_manager):
        """Test cleanup of non-existent repository."""
        result = storage_manager.cleanup_repository('JOB-NONEXISTENT')

        assert result is False

    def test_cleanup_repository_permission_error(self, storage_manager, mocker):
        """Test cleanup handles permission errors."""
        job_id = 'JOB-PERM'

        # Create repository
        repo_path = storage_manager.get_repository_path(job_id)
        repo_path.mkdir(parents=True)

        # Mock shutil.rmtree to raise permission error
        mocker.patch('shutil.rmtree', side_effect=PermissionError('Permission denied'))

        result = storage_manager.cleanup_repository(job_id)

        assert result is False

    def test_get_storage_info_empty(self, storage_manager):
        """Test storage info for empty storage."""
        info = storage_manager.get_storage_info()

        assert info['base_path'] == str(storage_manager.base_path)
        assert info['repositories_count'] == 0
        assert info['total_size_mb'] == 0

    def test_get_storage_info_with_repos(self, storage_manager):
        """Test storage info with repositories."""
        # Create some repositories with content
        for i in range(3):
            repo_path = storage_manager.get_repository_path(f'JOB-{i}')
            repo_path.mkdir(parents=True)
            (repo_path / 'file.txt').write_text('content' * 100)

        info = storage_manager.get_storage_info()

        assert info['repositories_count'] == 3
        assert info['total_size_mb'] >= 0

    def test_get_storage_info_nested_files(self, storage_manager):
        """Test storage info calculates nested file sizes."""
        job_id = 'JOB-NESTED'
        repo_path = storage_manager.get_repository_path(job_id)
        repo_path.mkdir(parents=True)

        # Create nested structure
        nested = repo_path / 'a' / 'b' / 'c'
        nested.mkdir(parents=True)
        (nested / 'deep_file.txt').write_text('deep content')
        (repo_path / 'root_file.txt').write_text('root content')

        info = storage_manager.get_storage_info()

        assert info['repositories_count'] == 1
        assert info['total_size_mb'] >= 0

    def test_storage_manager_with_nonexistent_path(self):
        """Test StorageManager creates path if it doesn't exist."""
        temp_dir = tempfile.mkdtemp()
        new_path = Path(temp_dir) / 'new' / 'path'

        try:
            manager = StorageManager(str(new_path))

            assert manager.base_path.exists()
            assert manager.repositories_path.exists()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_multiple_job_paths_are_unique(self, storage_manager):
        """Test that different job IDs get different paths."""
        path1 = storage_manager.get_repository_path('JOB-1')
        path2 = storage_manager.get_repository_path('JOB-2')

        assert path1 != path2
        assert path1.name == 'JOB-1'
        assert path2.name == 'JOB-2'

