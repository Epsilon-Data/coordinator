"""
Storage-related exceptions
"""
from typing import Optional
from exceptions.base import ExecutorError


class StorageError(ExecutorError):
    """Base exception for storage-related errors"""
    pass


class FileNotFoundError(StorageError):
    """Raised when a required file is not found"""
    
    def __init__(self, message: str, file_path: Optional[str] = None, **kwargs):
        super().__init__(message, **kwargs)
        if file_path:
            self.details['file_path'] = file_path


class StoragePermissionError(StorageError):
    """Raised when storage permission is denied"""
    
    def __init__(self, message: str, path: Optional[str] = None, operation: Optional[str] = None, **kwargs):
        super().__init__(message, **kwargs)
        if path:
            self.details['path'] = path
        if operation:
            self.details['operation'] = operation


class StorageQuotaExceededError(StorageError):
    """Raised when storage quota is exceeded"""
    
    def __init__(
        self,
        message: str,
        used_mb: Optional[float] = None,
        limit_mb: Optional[float] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        if used_mb is not None:
            self.details['used_mb'] = used_mb
        if limit_mb is not None:
            self.details['limit_mb'] = limit_mb