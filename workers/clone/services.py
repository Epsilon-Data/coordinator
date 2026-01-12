"""
Services for clone worker: Git operations and storage management.
"""
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class GitService:
    """Service for Git operations."""

    def __init__(self, timeout: int = 300):
        self.timeout = timeout

    def clone_repository(
        self,
        repo_url: str,
        target_path: Path,
        branch: Optional[str] = None
    ) -> None:
        """
        Clone a git repository to the target path.

        Args:
            repo_url: GitHub repository URL
            target_path: Path where repository should be cloned
            branch: Specific branch to clone (if provided)

        Raises:
            Exception: If clone fails or times out
        """
        # Convert repo name to full GitHub URL if needed
        if not repo_url.startswith(('http://', 'https://', 'git@')):
            repo_url = f"https://github.com/{repo_url}.git"

        logger.info(f"Cloning repository: {repo_url}")
        if branch:
            logger.info(f"Cloning branch: {branch}")
        logger.info(f"Target path: {target_path}")

        # Ensure parent directory exists and is writable
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove target directory if it exists (cleanup from failed attempts)
        if target_path.exists():
            logger.warning(f"Removing existing target directory: {target_path}")
            shutil.rmtree(target_path, ignore_errors=True)

        try:
            # Build git clone command
            # Use --template="" to skip git hooks templates (fixes Docker slim image issue)
            command = ["git", "clone", "--template="]

            # Add branch specification if provided
            if branch:
                command.extend(["--branch", branch])

            # Add depth and URL/path
            command.extend(["--depth", "1", repo_url, str(target_path)])

            # Run git clone command
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.timeout
            )

            if result.stdout:
                logger.info(f"Git output: {result.stdout}")

            logger.info("Repository cloned successfully")

        except subprocess.TimeoutExpired:
            error_msg = f"Repository clone timed out after {self.timeout} seconds"
            logger.error(error_msg)
            raise Exception(error_msg)

        except subprocess.CalledProcessError as e:
            error_msg = f"Git clone failed: {e.stderr}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def get_repo_info(self, repo_path: Path) -> Dict[str, Any]:
        """
        Get information about the cloned repository.

        Args:
            repo_path: Path to the cloned repository

        Returns:
            Dictionary with repo information
        """
        info = {
            "remote_url": None,
            "branch": None,
            "commit_hash": None,
            "commit_message": None,
            "commit_author": None,
            "commit_date": None
        }

        git_commands = [
            ("remote_url", ["git", "-C", str(repo_path), "remote", "get-url", "origin"]),
            ("branch", ["git", "-C", str(repo_path), "branch", "--show-current"]),
            ("commit_hash", ["git", "-C", str(repo_path), "rev-parse", "HEAD"]),
            ("commit_message", ["git", "-C", str(repo_path), "log", "-1", "--pretty=format:%s"]),
            ("commit_author", ["git", "-C", str(repo_path), "log", "-1", "--pretty=format:%an"]),
            ("commit_date", ["git", "-C", str(repo_path), "log", "-1", "--pretty=format:%ci"]),
        ]

        for key, command in git_commands:
            try:
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                info[key] = result.stdout.strip()
            except subprocess.CalledProcessError as e:
                logger.warning(f"Failed to get {key}: {e}")

        return info


class StorageManager:
    """Manages shared storage for repositories."""

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.repositories_path = self.base_path / "repositories"
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Ensure required directories exist."""
        directories = [
            self.base_path,
            self.repositories_path,
            self.base_path / "ai_analysis",
            self.base_path / "execution_results"
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {directory}")

    def get_repository_path(self, job_id: str) -> Path:
        """Get the path where repository should be stored."""
        return self.repositories_path / job_id

    def prepare_repository_directory(self, job_id: str) -> Path:
        """Prepare directory for repository clone."""
        repo_path = self.get_repository_path(job_id)

        # Remove existing directory if it exists
        if repo_path.exists():
            logger.warning(f"Repository directory already exists, removing: {repo_path}")
            shutil.rmtree(repo_path)

        # Create parent directory
        repo_path.parent.mkdir(parents=True, exist_ok=True)

        return repo_path

    def cleanup_repository(self, job_id: str) -> bool:
        """Clean up repository directory."""
        repo_path = self.get_repository_path(job_id)

        if repo_path.exists():
            try:
                shutil.rmtree(repo_path)
                logger.info(f"Cleaned up repository: {repo_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to cleanup repository: {e}")
                return False
        else:
            logger.warning(f"Repository not found for cleanup: {repo_path}")
            return False

    def get_storage_info(self) -> Dict[str, Any]:
        """Get information about storage usage."""
        info = {
            "base_path": str(self.base_path),
            "repositories_count": 0,
            "total_size_mb": 0.0
        }

        if self.repositories_path.exists():
            repos = list(self.repositories_path.iterdir())
            info["repositories_count"] = len(repos)

            total_size = 0
            for repo in repos:
                if repo.is_dir():
                    total_size += sum(
                        f.stat().st_size
                        for f in repo.rglob('*')
                        if f.is_file()
                    )

            info["total_size_mb"] = round(total_size / (1024 * 1024), 2)

        return info
