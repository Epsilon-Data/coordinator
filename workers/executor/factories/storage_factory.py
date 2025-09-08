"""
Factory for creating storage manager instances
"""
from typing import Optional
from pathlib import Path

from interfaces import IStorageManager
from config import Settings, get_settings
from exceptions import ConfigurationError
from utils import get_logger

logger = get_logger(__name__)


class StorageManagerFactory:
    """Factory for creating storage manager instances"""
    
    @staticmethod
    def create_manager(
        settings: Optional[Settings] = None,
        storage_type: str = "filesystem"
    ) -> IStorageManager:
        """
        Create a storage manager based on configuration
        
        Args:
            settings: Settings instance (uses default if None)
            storage_type: Type of storage manager to create
            
        Returns:
            Configured storage manager
            
        Raises:
            ConfigurationError: If manager cannot be created
        """
        if settings is None:
            settings = get_settings()
        
        try:
            if storage_type == "filesystem":
                return StorageManagerFactory._create_filesystem_manager(settings)
            elif storage_type == "s3":
                return StorageManagerFactory._create_s3_manager(settings)
            else:
                raise ConfigurationError(f"Unknown storage type: {storage_type}")
                
        except Exception as e:
            raise ConfigurationError(
                f"Failed to create storage manager: {e}",
                details={'storage_type': storage_type}
            )
    
    @staticmethod
    def _create_filesystem_manager(settings: Settings) -> IStorageManager:
        """Create filesystem-based storage manager"""
        from services.filesystem_storage import FilesystemStorageManager
        
        # Validate filesystem requirements
        storage_path = Path(settings.storage.shared_storage_path)
        if not storage_path.exists():
            raise ConfigurationError(f"Storage path does not exist: {storage_path}")
        
        if not storage_path.is_dir():
            raise ConfigurationError(f"Storage path is not a directory: {storage_path}")
        
        logger.debug(f"Creating FilesystemStorageManager with path: {storage_path}")
        return FilesystemStorageManager(
            base_path=storage_path,
            max_file_size_mb=settings.storage.max_file_size_mb,
            retention_days=settings.storage.artifacts_retention_days
        )
    
    @staticmethod
    def _create_s3_manager(settings: Settings) -> IStorageManager:
        """Create S3-based storage manager"""
        # Placeholder for S3 implementation
        raise NotImplementedError("S3 storage manager not yet implemented")
    
    @staticmethod
    def create_mock_manager() -> IStorageManager:
        """Create a mock storage manager for testing"""
        return MockStorageManager()


class MockStorageManager(IStorageManager):
    """Mock storage manager for testing"""
    
    def __init__(self):
        self._artifacts = {}
        logger.debug("MockStorageManager initialized")
    
    def get_repository_path(self, job_id: str) -> Path:
        """Mock repository path"""
        return Path(f"/mock/repos/{job_id}")
    
    def save_artifact(self, job_id: str, file_name: str, content: bytes,
                     artifact_type: str = "output") -> Path:
        """Mock save artifact"""
        key = f"{job_id}/{file_name}"
        self._artifacts[key] = {
            'content': content,
            'type': artifact_type,
            'size': len(content)
        }
        logger.debug(f"Mock saved artifact: {key}")
        return Path(f"/mock/artifacts/{key}")
    
    def get_artifact(self, job_id: str, file_name: str) -> Optional[bytes]:
        """Mock get artifact"""
        key = f"{job_id}/{file_name}"
        artifact = self._artifacts.get(key)
        return artifact['content'] if artifact else None
    
    def list_artifacts(self, job_id: str) -> list:
        """Mock list artifacts"""
        artifacts = []
        for key, data in self._artifacts.items():
            if key.startswith(f"{job_id}/"):
                artifacts.append({
                    'name': key.split('/', 1)[1],
                    'type': data['type'],
                    'size': data['size']
                })
        return artifacts
    
    def cleanup_old_artifacts(self, days: int) -> int:
        """Mock cleanup"""
        logger.debug(f"Mock cleanup of artifacts older than {days} days")
        return 0  # No cleanup in mock
    
    def get_storage_usage(self) -> dict:
        """Mock storage usage"""
        total_size = sum(artifact['size'] for artifact in self._artifacts.values())
        return {
            'total_files': len(self._artifacts),
            'total_size_bytes': total_size,
            'total_size_mb': total_size / (1024 * 1024)
        }