"""
Custom exceptions for the Epsilon Executor
"""
from exceptions.base import (
    ExecutorError,
    ConfigurationError,
    ValidationError,
    ExecutionError,
    EncryptionError,
    TimeoutError
)
from exceptions.enclave import (
    EnclaveError,
    EnclaveConnectionError,
    EnclaveDecryptionError,
    EnclaveExecutionError,
    EnclaveNotFoundError
)
from exceptions.storage import (
    StorageError,
    FileNotFoundError,
    StoragePermissionError,
    StorageQuotaExceededError
)

__all__ = [
    # Base exceptions
    'ExecutorError',
    'ConfigurationError',
    'ValidationError',
    'ExecutionError',
    'EncryptionError',
    'TimeoutError',
    
    # Enclave exceptions
    'EnclaveError',
    'EnclaveConnectionError',
    'EnclaveDecryptionError',
    'EnclaveExecutionError',
    'EnclaveNotFoundError',
    
    # Storage exceptions
    'StorageError',
    'FileNotFoundError',
    'StoragePermissionError',
    'StorageQuotaExceededError'
]