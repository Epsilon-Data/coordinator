import shutil
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class StorageManager:
    """Manages shared storage for repositories"""
    
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.repositories_path = self.base_path / "repositories"
        
        # Ensure directories exist
        self._ensure_directories()
        
    def _ensure_directories(self) -> None:
        """Ensure required directories exist"""
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
        """
        Get the path where repository should be stored
        
        Args:
            job_id: Unique job identifier
            
        Returns:
            Path to repository directory
        """
        return self.repositories_path / job_id
        
    def prepare_repository_directory(self, job_id: str) -> Path:
        """
        Prepare directory for repository clone
        
        Args:
            job_id: Unique job identifier
            
        Returns:
            Path to prepared directory
        """
        repo_path = self.get_repository_path(job_id)
        
        # Remove existing directory if it exists
        if repo_path.exists():
            logger.warning(f"Repository directory already exists, removing: {repo_path}")
            shutil.rmtree(repo_path)
            
        # Create parent directory
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        
        return repo_path
        
    def cleanup_repository(self, job_id: str) -> bool:
        """
        Clean up repository directory
        
        Args:
            job_id: Unique job identifier
            
        Returns:
            True if cleanup successful, False otherwise
        """
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
            
    def get_storage_info(self) -> dict:
        """Get information about storage usage"""
        info = {
            "base_path": str(self.base_path),
            "repositories_count": 0,
            "total_size_mb": 0
        }
        
        if self.repositories_path.exists():
            # Count repositories
            repos = list(self.repositories_path.iterdir())
            info["repositories_count"] = len(repos)
            
            # Calculate total size
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