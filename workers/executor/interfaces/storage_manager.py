"""
Abstract interface for storage managers
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from pathlib import Path


class IStorageManager(ABC):
    """Abstract interface for storage management"""
    
    @abstractmethod
    def get_repository_path(self, job_id: str) -> Path:
        """
        Get the repository path for a job
        
        Args:
            job_id: Job identifier
            
        Returns:
            Path to the repository
        """
        pass
    
    @abstractmethod
    def save_artifact(
        self,
        job_id: str,
        file_name: str,
        content: bytes,
        artifact_type: str = "output"
    ) -> Path:
        """
        Save a job artifact
        
        Args:
            job_id: Job identifier
            file_name: Name of the file
            content: File content
            artifact_type: Type of artifact (output, log, etc.)
            
        Returns:
            Path to the saved artifact
        """
        pass
    
    @abstractmethod
    def get_artifact(self, job_id: str, file_name: str) -> Optional[bytes]:
        """
        Retrieve a job artifact
        
        Args:
            job_id: Job identifier
            file_name: Name of the file
            
        Returns:
            File content or None if not found
        """
        pass
    
    @abstractmethod
    def list_artifacts(self, job_id: str) -> List[Dict[str, Any]]:
        """
        List all artifacts for a job
        
        Args:
            job_id: Job identifier
            
        Returns:
            List of artifact metadata
        """
        pass
    
    @abstractmethod
    def cleanup_old_artifacts(self, days: int) -> int:
        """
        Clean up artifacts older than specified days
        
        Args:
            days: Number of days to retain
            
        Returns:
            Number of artifacts cleaned up
        """
        pass
    
    @abstractmethod
    def get_storage_usage(self) -> Dict[str, Any]:
        """
        Get storage usage statistics
        
        Returns:
            Dictionary with usage statistics
        """
        pass