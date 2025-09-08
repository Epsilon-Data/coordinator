"""
Enclave-specific exceptions
"""
from typing import Optional
from exceptions.base import ExecutorError


class EnclaveError(ExecutorError):
    """Base exception for enclave-related errors"""
    pass


class EnclaveConnectionError(EnclaveError):
    """Raised when connection to enclave fails"""
    
    def __init__(self, message: str, cid: Optional[int] = None, port: Optional[int] = None, **kwargs):
        super().__init__(message, **kwargs)
        if cid:
            self.details['cid'] = cid
        if port:
            self.details['port'] = port


class EnclaveDecryptionError(EnclaveError):
    """Raised when enclave decryption fails"""
    
    def __init__(self, message: str, method: Optional[str] = None, **kwargs):
        super().__init__(message, **kwargs)
        if method:
            self.details['encryption_method'] = method


class EnclaveExecutionError(EnclaveError):
    """Raised when script execution in enclave fails"""
    
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
    """Raised when enclave is not running or cannot be found"""
    
    def __init__(self, message: str = "No running enclave found", **kwargs):
        super().__init__(message, **kwargs)