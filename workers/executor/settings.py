"""
Configuration management for the Executor worker.
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
from functools import lru_cache

from dotenv import load_dotenv

# Find project root and load .env
_current_dir = Path(__file__).resolve().parent
_project_root = _current_dir.parent.parent
_env_file = _project_root / '.env'
if _env_file.exists():
    load_dotenv(_env_file)


@dataclass
class AWSConfig:
    """AWS-specific configuration.

    Note: AWS credentials (access key, secret key, session token) are resolved
    automatically by boto3 via its standard credential chain (env vars, instance
    role, config files). They are NOT stored here.
    """
    region: str = field(default_factory=lambda: os.getenv('KMS_REGION', 'ap-southeast-2'))
    kms_key_arn: str = field(default_factory=lambda: os.getenv('AWS_KMS_KEY_ARN', ''))


@dataclass
class EnclaveConfig:
    """Nitro Enclave configuration."""
    memory_mb: int = field(default_factory=lambda: int(os.getenv('ENCLAVE_MEMORY_MB', '4096')))
    cpu_count: int = field(default_factory=lambda: int(os.getenv('ENCLAVE_CPU_COUNT', '2')))
    eif_path: str = field(default_factory=lambda: os.getenv('ENCLAVE_EIF_PATH', '/opt/enclaves/executor.eif'))
    kms_proxy_port: int = field(default_factory=lambda: int(os.getenv('KMS_PROXY_PORT', '8000')))
    vsock_port: int = field(default_factory=lambda: int(os.getenv('ENCLAVE_VSOCK_PORT', '5005')))
    debug_mode: bool = field(default_factory=lambda: os.getenv('ENCLAVE_DEBUG', 'false').lower() in ('true', '1', 'yes'))
    use_local_client: bool = field(default_factory=lambda: os.getenv('USE_LOCAL_ENCLAVE', 'false').lower() in ('true', '1', 'yes'))
    # External enclave mode: enclave is managed separately (not launched by executor)
    use_external_enclave: bool = field(default_factory=lambda: os.getenv('USE_EXTERNAL_ENCLAVE', 'false').lower() in ('true', '1', 'yes'))
    external_enclave_cid: Optional[int] = field(default_factory=lambda: int(os.getenv('ENCLAVE_CID', '0')) if os.getenv('ENCLAVE_CID') else None)


@dataclass
class StorageConfig:
    """Storage configuration."""
    shared_storage_path: str = field(default_factory=lambda: os.getenv('SHARED_STORAGE_PATH', '/shared/epsilon'))
    artifacts_retention_days: int = field(default_factory=lambda: int(os.getenv('ARTIFACTS_RETENTION_DAYS', '7')))
    max_file_size_mb: int = field(default_factory=lambda: int(os.getenv('MAX_FILE_SIZE_MB', '100')))


@dataclass
class PollingConfig:
    """Polling configuration for job acquisition."""
    interval_seconds: int = field(default_factory=lambda: int(os.getenv('POLLING_INTERVAL', '5')))
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv('POLLING_TIMEOUT_SECONDS', '30')))
    max_retries: int = field(default_factory=lambda: int(os.getenv('POLLING_MAX_RETRIES', '3')))


@dataclass
class CoordinatorConfig:
    """Coordinator API configuration."""
    base_url: str = field(default_factory=lambda: os.getenv('COORDINATOR_BASE_URL', 'http://api-server:8001'))
    api_key: str = field(default_factory=lambda: os.getenv('COORDINATOR_API_KEY', ''))
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv('COORDINATOR_TIMEOUT_SECONDS', '30')))


@dataclass
class MiddlewareConfig:
    """Middleware configuration for CSV fetching."""
    endpoint_url: Optional[str] = field(default_factory=lambda: os.getenv('MIDDLEWARE_ENDPOINT_URL'))
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv('MIDDLEWARE_TIMEOUT_SECONDS', '60')))
    mode: str = field(default_factory=lambda: os.getenv('MIDDLEWARE_MODE', 'local'))  # "aws" or "local"
    aws_region: str = field(default_factory=lambda: os.getenv('AWS_REGION', 'ap-southeast-2'))
    use_direct_db: bool = field(default_factory=lambda: os.getenv('MIDDLEWARE_DIRECT_DB', 'false').lower() == 'true')

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint_url)


@dataclass
class ProxyConfig:
    """Proxy tunnel configuration for data owner connections."""
    enabled: bool = field(default_factory=lambda: os.getenv('PROXY_ENABLED', 'true').lower() == 'true')
    rathole_host: str = field(default_factory=lambda: os.getenv('RATHOLE_HOST', 'localhost'))
    request_timeout_seconds: int = field(default_factory=lambda: int(os.getenv('PROXY_REQUEST_TIMEOUT', '120')))


@dataclass
class ExecutionConfig:
    """Execution-specific configuration."""
    script_timeout_seconds: int = field(default_factory=lambda: int(os.getenv('SCRIPT_TIMEOUT', '300')))
    max_output_size_mb: int = field(default_factory=lambda: int(os.getenv('MAX_OUTPUT_SIZE_MB', '10')))
    allowed_script_extensions: list = field(default_factory=lambda: ['.py'])
    python_path: str = field(default_factory=lambda: os.getenv('PYTHON_PATH', 'python3'))


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = field(default_factory=lambda: os.getenv('LOG_LEVEL', 'INFO'))
    format: str = field(default_factory=lambda: os.getenv(
        'LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    file_path: Optional[str] = field(default_factory=lambda: os.getenv('LOG_FILE'))
    max_file_size_mb: int = field(default_factory=lambda: int(os.getenv('LOG_MAX_SIZE_MB', '100')))
    backup_count: int = field(default_factory=lambda: int(os.getenv('LOG_BACKUP_COUNT', '5')))


@dataclass
class Settings:
    """Main settings container."""
    aws: AWSConfig = field(default_factory=AWSConfig)
    enclave: EnclaveConfig = field(default_factory=EnclaveConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    coordinator: CoordinatorConfig = field(default_factory=CoordinatorConfig)
    middleware: MiddlewareConfig = field(default_factory=MiddlewareConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)

    worker_id: str = field(default_factory=lambda: os.getenv('WORKER_ID', 'executor-001'))
    environment: str = field(default_factory=lambda: os.getenv('ENVIRONMENT', 'development'))
    job_fetch_mode: str = field(default_factory=lambda: os.getenv('JOB_FETCH_MODE', 'polling'))

    def to_dict(self) -> Dict[str, Any]:
        """Return settings as dict with sensitive fields redacted."""
        coordinator = {**self.coordinator.__dict__}
        coordinator['api_key'] = '***' if coordinator.get('api_key') else ''
        return {
            'aws': {'region': self.aws.region, 'kms_key_arn': '***' if self.aws.kms_key_arn else ''},
            'enclave': self.enclave.__dict__,
            'storage': self.storage.__dict__,
            'execution': self.execution.__dict__,
            'logging': self.logging.__dict__,
            'polling': self.polling.__dict__,
            'coordinator': coordinator,
            'middleware': {k: v for k, v in self.middleware.__dict__.items() if k != 'endpoint_url'},
            'proxy': self.proxy.__dict__,
            'worker_id': self.worker_id,
            'environment': self.environment,
            'job_fetch_mode': self.job_fetch_mode
        }

    def is_production(self) -> bool:
        return self.environment.lower() in ['production', 'prod']

    def is_development(self) -> bool:
        return self.environment.lower() in ['development', 'dev']


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance (singleton)."""
    return Settings()


# Validation functions
def validate_environment(settings: Settings) -> Tuple[bool, List[str]]:
    """Validate the environment configuration."""
    errors = []

    # Skip KMS and EIF validation for local client or external enclave mode
    skip_enclave_validation = (
        settings.enclave.use_local_client or
        settings.enclave.use_external_enclave
    )

    if not settings.aws.kms_key_arn and not skip_enclave_validation:
        errors.append("AWS_KMS_KEY_ARN is required when not using local client or external enclave")

    if not skip_enclave_validation:
        eif_path = Path(settings.enclave.eif_path)
        if not eif_path.exists():
            errors.append(f"Enclave EIF file not found: {settings.enclave.eif_path}")

    # Validate external enclave CID is set when using external mode
    if settings.enclave.use_external_enclave and not settings.enclave.external_enclave_cid:
        errors.append("ENCLAVE_CID is required when using external enclave mode")

    storage_path = Path(settings.storage.shared_storage_path)
    if not storage_path.exists():
        errors.append(f"Shared storage path does not exist: {settings.storage.shared_storage_path}")

    if not settings.middleware.endpoint_url:
        errors.append("MIDDLEWARE_ENDPOINT_URL is required")

    if settings.execution.script_timeout_seconds < 1:
        errors.append("Script timeout must be at least 1 second")

    if settings.execution.max_output_size_mb < 1:
        errors.append("Max output size must be at least 1 MB")

    if settings.is_production():
        if settings.enclave.debug_mode:
            errors.append("Debug mode should be disabled in production")
        if settings.logging.level == 'DEBUG':
            errors.append("Debug logging should not be used in production")

    return len(errors) == 0, errors


def check_permissions(settings: Settings) -> Tuple[bool, List[str]]:
    """Check file system permissions."""
    errors = []

    storage_path = Path(settings.storage.shared_storage_path)
    if storage_path.exists() and not os.access(storage_path, os.W_OK):
        errors.append(f"Storage path is not writable: {storage_path}")

    if settings.logging.file_path:
        log_dir = Path(settings.logging.file_path).parent
        if not log_dir.exists():
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"Cannot create log directory: {e}")
        elif not os.access(log_dir, os.W_OK):
            errors.append(f"Log directory is not writable: {log_dir}")

    return len(errors) == 0, errors


def validate_and_raise(settings: Settings) -> None:
    """Validate configuration and raise exception if invalid."""
    from workers.executor.exceptions import ConfigurationError

    env_valid, env_errors = validate_environment(settings)
    perm_valid, perm_errors = check_permissions(settings)

    all_errors = env_errors + perm_errors

    if not (env_valid and perm_valid):
        error_msg = "Configuration validation failed:\n"
        error_msg += "\n".join(f"  - {error}" for error in all_errors)
        raise ConfigurationError(error_msg)
