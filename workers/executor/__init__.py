"""
Executor worker package.

Flat structure:
- models.py: Data models (JobExecutionRequest, ExecutionResult, BuildConfig, etc.)
- exceptions.py: Exception classes
- interfaces.py: Abstract interfaces (IExecutor, IEnclaveClient, IMiddlewareClient)
- settings.py: Configuration and validation
- executor.py: SecureExecutor implementation
- factories.py: Factory classes for dependency injection
- clients.py: Client implementations (EnclaveClient, MiddlewareClient)
- services.py: Services (BuildValidator, ZipService, CryptoService)
- utils.py: Utilities (logging, decorators)
- worker.py: Main worker class
"""
from workers.executor.models import (
    JobStatus,
    JobExecutionRequest,
    ExecutionResult,
    DatasetConfig,
    BuildConfig,
)
from workers.executor.exceptions import (
    ExecutorError,
    ConfigurationError,
    ValidationError,
    BuildValidationError,
    ExecutionError,
    EncryptionError,
    EnclaveError,
    EnclaveConnectionError,
    EnclaveDecryptionError,
    EnclaveExecutionError,
    EnclaveNotFoundError,
)
from workers.executor.interfaces import (
    IExecutor,
    IEnclaveClient,
    IMiddlewareClient,
    MiddlewareRequest,
    MiddlewareResponse,
)
from workers.executor.settings import Settings, get_settings
from workers.executor.executor import SecureExecutor
from workers.executor.factories import (
    ExecutorFactory,
    EnclaveClientFactory,
    MiddlewareClientFactory,
)
# Note: ExecutorWorker not imported here to avoid circular import when running as module
# Use: from workers.executor.worker import ExecutorWorker

__all__ = [
    # Models
    'JobStatus',
    'JobExecutionRequest',
    'ExecutionResult',
    'DatasetConfig',
    'BuildConfig',
    # Exceptions
    'ExecutorError',
    'ConfigurationError',
    'ValidationError',
    'BuildValidationError',
    'ExecutionError',
    'EncryptionError',
    'EnclaveError',
    'EnclaveConnectionError',
    'EnclaveDecryptionError',
    'EnclaveExecutionError',
    'EnclaveNotFoundError',
    # Interfaces
    'IExecutor',
    'IEnclaveClient',
    'IMiddlewareClient',
    'MiddlewareRequest',
    'MiddlewareResponse',
    # Settings
    'Settings',
    'get_settings',
    # Implementation
    'SecureExecutor',
    # Factories
    'ExecutorFactory',
    'EnclaveClientFactory',
    'MiddlewareClientFactory',
]
