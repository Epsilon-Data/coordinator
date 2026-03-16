"""
Exception classes for executor worker.
"""
from typing import Optional, Dict, Any


class ExecutorError(Exception):
    """Base exception for all executor errors."""

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
        """Convert exception to dictionary."""
        return {
            'error': self.error_code,
            'message': self.message,
            'details': self.details
        }


class ConfigurationError(ExecutorError):
    """Raised when configuration is invalid or missing."""
    pass


class BuildValidationError(ExecutorError):
    """Raised when build folder validation fails."""
    pass


# Enclave-specific exceptions
class EnclaveError(ExecutorError):
    """Base exception for enclave-related errors."""
    pass


class EnclaveConnectionError(EnclaveError):
    """Raised when connection to enclave fails."""

    def __init__(self, message: str, cid: Optional[int] = None, port: Optional[int] = None, **kwargs):
        super().__init__(message, **kwargs)
        if cid:
            self.details['cid'] = cid
        if port:
            self.details['port'] = port


class EnclaveExecutionError(EnclaveError):
    """Raised when script execution in enclave fails."""

    def __init__(
        self,
        message: str,
        script_path: Optional[str] = None,
        is_bundle: bool = False,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        if script_path:
            self.details['script_path'] = script_path
        self.details['is_bundle'] = is_bundle


class EnclaveNotFoundError(EnclaveError):
    """Raised when enclave is not running."""

    def __init__(self, message: str = "No running enclave found", **kwargs):
        super().__init__(message, **kwargs)
