import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GitService:
    """Service for Git operations"""
    
    def __init__(self, timeout: int = 300):
        self.timeout = timeout
        
    def clone_repository(self, repo_url: str, target_path: Path, branch: Optional[str] = None) -> None:
        """
        Clone a git repository to the target path
        
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
        
        # Ensure parent directory exists
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Build git clone command
            command = ["git", "clone"]
            
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
            
    def get_repo_info(self, repo_path: Path) -> dict:
        """
        Get information about the cloned repository
        
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
        
        try:
            # Get remote URL
            result = subprocess.run(
                ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=True
            )
            info["remote_url"] = result.stdout.strip()
            
            # Get current branch
            result = subprocess.run(
                ["git", "-C", str(repo_path), "branch", "--show-current"],
                capture_output=True,
                text=True,
                check=True
            )
            info["branch"] = result.stdout.strip()
            
            # Get commit hash
            result = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True
            )
            info["commit_hash"] = result.stdout.strip()
            
            # Get commit message
            result = subprocess.run(
                ["git", "-C", str(repo_path), "log", "-1", "--pretty=format:%s"],
                capture_output=True,
                text=True,
                check=True
            )
            info["commit_message"] = result.stdout.strip()
            
            # Get commit author
            result = subprocess.run(
                ["git", "-C", str(repo_path), "log", "-1", "--pretty=format:%an"],
                capture_output=True,
                text=True,
                check=True
            )
            info["commit_author"] = result.stdout.strip()
            
            # Get commit date
            result = subprocess.run(
                ["git", "-C", str(repo_path), "log", "-1", "--pretty=format:%ci"],
                capture_output=True,
                text=True,
                check=True
            )
            info["commit_date"] = result.stdout.strip()
            
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to get repo info: {e}")
            
        return info