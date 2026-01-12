"""
Tests for workers/clone/services.py - GitService
"""
import subprocess
from pathlib import Path
import tempfile
import shutil
import pytest
from unittest.mock import MagicMock, patch

from workers.clone.services import GitService


class TestGitService:
    """Test cases for GitService class."""

    @pytest.fixture
    def git_service(self):
        """Create GitService instance."""
        return GitService(timeout=60)

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_initialization(self):
        """Test GitService initializes with timeout."""
        service = GitService(timeout=120)
        assert service.timeout == 120

    def test_initialization_default_timeout(self):
        """Test GitService uses default timeout."""
        service = GitService()
        assert service.timeout == 300

    @patch('workers.clone.services.subprocess.run')
    def test_clone_repository_success(self, mock_run, git_service, temp_dir):
        """Test successful repository clone."""
        mock_run.return_value = MagicMock(stdout='Cloned successfully', returncode=0)

        target_path = temp_dir / 'repo'
        git_service.clone_repository('https://github.com/user/repo.git', target_path)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert 'git' in call_args
        assert 'clone' in call_args
        assert 'https://github.com/user/repo.git' in call_args

    @patch('workers.clone.services.subprocess.run')
    def test_clone_repository_with_branch(self, mock_run, git_service, temp_dir):
        """Test cloning with specific branch."""
        mock_run.return_value = MagicMock(stdout='', returncode=0)

        target_path = temp_dir / 'repo'
        git_service.clone_repository('https://github.com/user/repo.git', target_path, branch='develop')

        call_args = mock_run.call_args[0][0]
        assert '--branch' in call_args
        assert 'develop' in call_args

    @patch('workers.clone.services.subprocess.run')
    def test_clone_repository_short_name(self, mock_run, git_service, temp_dir):
        """Test cloning with short repo name (owner/repo)."""
        mock_run.return_value = MagicMock(stdout='', returncode=0)

        target_path = temp_dir / 'repo'
        git_service.clone_repository('owner/repo', target_path)

        call_args = mock_run.call_args[0][0]
        # Should convert to full URL
        assert 'https://github.com/owner/repo.git' in call_args

    @patch('workers.clone.services.subprocess.run')
    def test_clone_repository_uses_depth(self, mock_run, git_service, temp_dir):
        """Test that shallow clone is used."""
        mock_run.return_value = MagicMock(stdout='', returncode=0)

        target_path = temp_dir / 'repo'
        git_service.clone_repository('https://github.com/user/repo.git', target_path)

        call_args = mock_run.call_args[0][0]
        assert '--depth' in call_args
        assert '1' in call_args

    @patch('workers.clone.services.subprocess.run')
    def test_clone_repository_timeout(self, mock_run, git_service, temp_dir):
        """Test handling of clone timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='git clone', timeout=60)

        target_path = temp_dir / 'repo'

        with pytest.raises(Exception, match='timed out'):
            git_service.clone_repository('https://github.com/user/repo.git', target_path)

    @patch('workers.clone.services.subprocess.run')
    def test_clone_repository_failure(self, mock_run, git_service, temp_dir):
        """Test handling of clone failure."""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, 'git clone', stderr='fatal: repository not found'
        )

        target_path = temp_dir / 'repo'

        with pytest.raises(Exception, match='Git clone failed'):
            git_service.clone_repository('https://github.com/user/nonexistent.git', target_path)

    @patch('workers.clone.services.subprocess.run')
    def test_clone_creates_parent_directory(self, mock_run, git_service, temp_dir):
        """Test that parent directory is created."""
        mock_run.return_value = MagicMock(stdout='', returncode=0)

        target_path = temp_dir / 'deep' / 'nested' / 'repo'
        git_service.clone_repository('https://github.com/user/repo.git', target_path)

        # Parent should exist
        assert target_path.parent.exists()

    @patch('workers.clone.services.subprocess.run')
    def test_get_repo_info_success(self, mock_run, git_service, temp_dir):
        """Test getting repository information."""
        # Mock different git commands
        def run_side_effect(cmd, **kwargs):
            mock_result = MagicMock()
            mock_result.stdout = ''
            mock_result.returncode = 0

            # Join cmd list to string for easier matching
            cmd_str = ' '.join(cmd)

            if 'get-url' in cmd_str:
                mock_result.stdout = 'https://github.com/user/repo.git\n'
            elif '--show-current' in cmd_str:
                mock_result.stdout = 'main\n'
            elif 'rev-parse' in cmd_str:
                mock_result.stdout = 'abc123def456\n'
            elif '%s' in cmd_str:
                mock_result.stdout = 'Commit message\n'
            elif '%an' in cmd_str:
                mock_result.stdout = 'Author Name\n'
            elif '%ci' in cmd_str:
                mock_result.stdout = '2024-01-01 12:00:00 +0000\n'

            return mock_result

        mock_run.side_effect = run_side_effect

        info = git_service.get_repo_info(temp_dir)

        assert info['remote_url'] == 'https://github.com/user/repo.git'
        assert info['branch'] == 'main'
        assert info['commit_hash'] == 'abc123def456'
        assert info['commit_message'] == 'Commit message'
        assert info['commit_author'] == 'Author Name'

    @patch('workers.clone.services.subprocess.run')
    def test_get_repo_info_failure_returns_defaults(self, mock_run, git_service, temp_dir):
        """Test that failures return default values."""
        mock_run.side_effect = subprocess.CalledProcessError(1, 'git', stderr='error')

        info = git_service.get_repo_info(temp_dir)

        assert info['remote_url'] is None
        assert info['branch'] is None
        assert info['commit_hash'] is None

    def test_clone_full_https_url(self, git_service, temp_dir):
        """Test that full HTTPS URL is used as-is."""
        with patch('workers.clone.services.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stdout='', returncode=0)

            target_path = temp_dir / 'repo'
            git_service.clone_repository('https://github.com/user/repo.git', target_path)

            call_args = mock_run.call_args[0][0]
            assert 'https://github.com/user/repo.git' in call_args

    def test_clone_git_ssh_url(self, git_service, temp_dir):
        """Test that SSH URL is used as-is."""
        with patch('workers.clone.services.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stdout='', returncode=0)

            target_path = temp_dir / 'repo'
            git_service.clone_repository('git@github.com:user/repo.git', target_path)

            call_args = mock_run.call_args[0][0]
            assert 'git@github.com:user/repo.git' in call_args

