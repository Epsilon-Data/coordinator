"""
Executor worker package.

Flat structure:
- models.py: Data models (JobExecutionRequest, ExecutionResult, BuildConfig, etc.)
- exceptions.py: Exception classes
- interfaces.py: Abstract interfaces (IExecutor, IEnclaveClient, IMiddlewareClient)
- constants.py: String constants for enclave operations and middleware modes
- settings.py: Configuration and validation
- executor.py: SecureExecutor implementation
- factories.py: Factory classes for dependency injection
- clients.py: Client implementations (EnclaveClient, MiddlewareClient, ProxyClient)
- services.py: Services (BuildValidator, ZipService, CryptoService)
- utils.py: Utilities (logging)
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
    BuildValidationError,
    EnclaveError,
    EnclaveConnectionError,
    EnclaveExecutionError,
    EnclaveNotFoundError,
)
from workers.executor.interfaces import (
    IExecutor,
    IEnclaveClient,
    IMiddlewareClient,
    MiddlewareRequest,
    MiddlewareResponse,
    ProxyResponse,
    ProxyInfo,
)
from workers.executor.constants import EnclaveOperations, MiddlewareModes
from workers.executor.settings import Settings, get_settings
from workers.executor.executor import SecureExecutor
from workers.executor.factories import (
    ExecutorFactory,
    EnclaveClientFactory,
    MiddlewareClientFactory,
    ProxyClientFactory,
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
    'BuildValidationError',
    'EnclaveError',
    'EnclaveConnectionError',
    'EnclaveExecutionError',
    'EnclaveNotFoundError',
    # Interfaces
    'IExecutor',
    'IEnclaveClient',
    'IMiddlewareClient',
    'MiddlewareRequest',
    'MiddlewareResponse',
    'ProxyResponse',
    'ProxyInfo',
    # Constants
    'EnclaveOperations',
    'MiddlewareModes',
    # Settings
    'Settings',
    'get_settings',
    # Implementation
    'SecureExecutor',
    # Factories
    'ExecutorFactory',
    'EnclaveClientFactory',
    'MiddlewareClientFactory',
    'ProxyClientFactory',
]
