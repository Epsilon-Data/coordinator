"""
Base exception classes for the Executor
"""
from typing import Optional, Dict, Any


class ExecutorError(Exception):
    """Base exception for all Executor errors"""
    
    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for serialization"""
        return {
            'error': self.error_code,
            'message': self.message,
            'details': self.details
        }


class ConfigurationError(ExecutorError):
    """Raised when configuration is invalid or missing"""
    pass


class ValidationError(ExecutorError):
    """Raised when input validation fails"""
    
    def __init__(self, message: str, field: Optional[str] = None, **kwargs):
        super().__init__(message, **kwargs)
        if field:
            self.details['field'] = field


class ExecutionError(ExecutorError):
    """Raised when script execution fails"""
    
    def __init__(
        self,
        message: str,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        if exit_code is not None:
            self.details['exit_code'] = exit_code
        if stdout:
            self.details['stdout'] = stdout
        if stderr:
            self.details['stderr'] = stderr


class EncryptionError(ExecutorError):
    """Raised when encryption/decryption fails"""
    
    def __init__(self, message: str, operation: Optional[str] = None, **kwargs):
        super().__init__(message, **kwargs)
        if operation:
            self.details['operation'] = operation


class TimeoutError(ExecutorError):
    """Raised when an operation times out"""
    
    def __init__(self, message: str, timeout_seconds: Optional[int] = None, **kwargs):
        super().__init__(message, **kwargs)
        if timeout_seconds:
            self.details['timeout_seconds'] = timeout_seconds