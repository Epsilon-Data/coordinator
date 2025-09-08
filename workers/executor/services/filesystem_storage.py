"""
Filesystem-based storage manager implementation
"""
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

from interfaces import IStorageManager
from exceptions import StorageError, FileNotFoundError, StoragePermissionError
from utils import get_logger

logger = get_logger(__name__)


class FilesystemStorageManager(IStorageManager):
    """Filesystem-based storage manager"""
    
    def __init__(
        self,
        base_path: Path,
        max_file_size_mb: float = 100.0,
        retention_days: int = 7
    ):
        """
        Initialize filesystem storage manager
        
        Args:
            base_path: Base path for storage
            max_file_size_mb: Maximum file size in MB
            retention_days: Artifact retention period in days
        """
        self._base_path = Path(base_path)
        self._max_file_size_bytes = int(max_file_size_mb * 1024 * 1024)
        self._retention_days = retention_days
        
        # Ensure base path exists
        self._base_path.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        self._repos_path = self._base_path / "repositories"
        self._artifacts_path = self._base_path / "artifacts"
        self._repos_path.mkdir(exist_ok=True)
        self._artifacts_path.mkdir(exist_ok=True)
        
        logger.info(f"FilesystemStorageManager initialized with base path: {self._base_path}")
    
    def get_repository_path(self, job_id: str) -> Path:
        """Get the repository path for a job"""
        return self._repos_path / job_id
    
    def save_artifact(
        self,
        job_id: str,
        file_name: str,
        content: bytes,
        artifact_type: str = "output"
    ) -> Path:
        """Save a job artifact"""
        if len(content) > self._max_file_size_bytes:
            raise StorageError(
                f"File size ({len(content)} bytes) exceeds limit ({self._max_file_size_bytes} bytes)",
                details={
                    'file_size_mb': len(content) / (1024 * 1024),
                    'limit_mb': self._max_file_size_bytes / (1024 * 1024)
                }
            )
        
        # Create job artifact directory
        job_artifacts_dir = self._artifacts_path / job_id
        job_artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        # Create type subdirectory
        type_dir = job_artifacts_dir / artifact_type
        type_dir.mkdir(exist_ok=True)
        
        # Save the file
        artifact_path = type_dir / file_name
        
        try:
            with open(artifact_path, 'wb') as f:
                f.write(content)
            
            # Save metadata
            self._save_artifact_metadata(artifact_path, artifact_type, len(content))
            
            logger.debug(f"Saved artifact: {artifact_path}")
            return artifact_path
            
        except PermissionError as e:
            raise StoragePermissionError(
                f"Permission denied writing to {artifact_path}",
                path=str(artifact_path),
                operation="write"
            )
        except Exception as e:
            raise StorageError(f"Failed to save artifact: {e}")
    
    def get_artifact(self, job_id: str, file_name: str) -> Optional[bytes]:
        """Retrieve a job artifact"""
        # Search in all artifact type directories
        job_artifacts_dir = self._artifacts_path / job_id
        
        if not job_artifacts_dir.exists():
            return None
        
        # Look for the file in all subdirectories
        for type_dir in job_artifacts_dir.iterdir():
            if type_dir.is_dir():
                artifact_path = type_dir / file_name
                if artifact_path.exists():
                    try:
                        with open(artifact_path, 'rb') as f:
                            return f.read()
                    except Exception as e:
                        logger.error(f"Failed to read artifact {artifact_path}: {e}")
                        continue
        
        return None
    
    def list_artifacts(self, job_id: str) -> List[Dict[str, Any]]:
        """List all artifacts for a job"""
        artifacts = []
        job_artifacts_dir = self._artifacts_path / job_id
        
        if not job_artifacts_dir.exists():
            return artifacts
        
        for type_dir in job_artifacts_dir.iterdir():
            if not type_dir.is_dir():
                continue
                
            artifact_type = type_dir.name
            
            for artifact_file in type_dir.iterdir():
                if artifact_file.is_file() and not artifact_file.name.endswith('.meta'):
                    try:
                        stat = artifact_file.stat()
                        metadata = self._load_artifact_metadata(artifact_file)
                        
                        artifacts.append({
                            'name': artifact_file.name,
                            'type': artifact_type,
                            'size': stat.st_size,
                            'created_at': datetime.fromtimestamp(stat.st_ctime).isoformat(),
                            'modified_at': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            'path': str(artifact_file),
                            'metadata': metadata
                        })
                    except Exception as e:
                        logger.warning(f"Failed to get info for artifact {artifact_file}: {e}")
        
        return sorted(artifacts, key=lambda x: x['created_at'])
    
    def cleanup_old_artifacts(self, days: int) -> int:
        """Clean up artifacts older than specified days"""
        cutoff_date = datetime.now() - timedelta(days=days)
        cleaned_count = 0
        
        logger.info(f"Starting cleanup of artifacts older than {days} days")
        
        for job_dir in self._artifacts_path.iterdir():
            if not job_dir.is_dir():
                continue
            
            job_cleaned = 0
            
            for type_dir in job_dir.iterdir():
                if not type_dir.is_dir():
                    continue
                
                for artifact_file in type_dir.iterdir():
                    if not artifact_file.is_file():
                        continue
                    
                    try:
                        file_time = datetime.fromtimestamp(artifact_file.stat().st_mtime)
                        if file_time < cutoff_date:
                            artifact_file.unlink()
                            # Also remove metadata file
                            meta_file = artifact_file.with_suffix(artifact_file.suffix + '.meta')
                            if meta_file.exists():
                                meta_file.unlink()
                            job_cleaned += 1
                            cleaned_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to clean up {artifact_file}: {e}")
            
            # Remove empty directories
            try:
                for type_dir in job_dir.iterdir():
                    if type_dir.is_dir() and not any(type_dir.iterdir()):
                        type_dir.rmdir()
                
                if not any(job_dir.iterdir()):
                    job_dir.rmdir()
                    logger.debug(f"Removed empty job directory: {job_dir}")
                    
            except Exception as e:
                logger.warning(f"Failed to remove empty directories for {job_dir}: {e}")
            
            if job_cleaned > 0:
                logger.debug(f"Cleaned {job_cleaned} artifacts for job {job_dir.name}")
        
        logger.info(f"Cleanup completed. Removed {cleaned_count} artifacts")
        return cleaned_count
    
    def get_storage_usage(self) -> Dict[str, Any]:
        """Get storage usage statistics"""
        total_size = 0
        total_files = 0
        
        def calculate_directory_size(path: Path) -> tuple[int, int]:
            """Calculate total size and file count for a directory"""
            size = 0
            files = 0
            
            try:
                for item in path.rglob('*'):
                    if item.is_file():
                        size += item.stat().st_size
                        files += 1
            except Exception as e:
                logger.warning(f"Failed to calculate size for {path}: {e}")
            
            return size, files
        
        # Calculate artifacts usage
        artifacts_size, artifacts_files = calculate_directory_size(self._artifacts_path)
        
        # Calculate repositories usage
        repos_size, repos_files = calculate_directory_size(self._repos_path)
        
        total_size = artifacts_size + repos_size
        total_files = artifacts_files + repos_files
        
        return {
            'total_size_bytes': total_size,
            'total_size_mb': total_size / (1024 * 1024),
            'total_files': total_files,
            'artifacts': {
                'size_bytes': artifacts_size,
                'size_mb': artifacts_size / (1024 * 1024),
                'files': artifacts_files
            },
            'repositories': {
                'size_bytes': repos_size,
                'size_mb': repos_size / (1024 * 1024),
                'files': repos_files
            },
            'base_path': str(self._base_path)
        }
    
    def _save_artifact_metadata(self, artifact_path: Path, artifact_type: str, size: int):
        """Save metadata for an artifact"""
        metadata = {
            'type': artifact_type,
            'size': size,
            'created_at': datetime.utcnow().isoformat(),
            'original_name': artifact_path.name
        }
        
        meta_path = artifact_path.with_suffix(artifact_path.suffix + '.meta')
        try:
            with open(meta_path, 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save metadata for {artifact_path}: {e}")
    
    def _load_artifact_metadata(self, artifact_path: Path) -> Dict[str, Any]:
        """Load metadata for an artifact"""
        meta_path = artifact_path.with_suffix(artifact_path.suffix + '.meta')
        
        if not meta_path.exists():
            return {}
        
        try:
            with open(meta_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load metadata for {artifact_path}: {e}")
            return {}