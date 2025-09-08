"""
Central configuration management for the Executor
"""
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from functools import lru_cache


@dataclass
class AWSConfig:
    """AWS-specific configuration"""
    region: str = field(default_factory=lambda: os.getenv('KMS_REGION', 'ap-southeast-2'))
    kms_key_arn: str = field(default_factory=lambda: os.getenv('AWS_KMS_KEY_ARN', ''))
    access_key_id: Optional[str] = field(default_factory=lambda: os.getenv('AWS_ACCESS_KEY_ID'))
    secret_access_key: Optional[str] = field(default_factory=lambda: os.getenv('AWS_SECRET_ACCESS_KEY'))
    session_token: Optional[str] = field(default_factory=lambda: os.getenv('AWS_SESSION_TOKEN'))


@dataclass
class EnclaveConfig:
    """Nitro Enclave configuration"""
    memory_mb: int = field(default_factory=lambda: int(os.getenv('ENCLAVE_MEMORY_MB', '4096')))
    cpu_count: int = field(default_factory=lambda: int(os.getenv('ENCLAVE_CPU_COUNT', '2')))
    eif_path: str = field(default_factory=lambda: os.getenv('ENCLAVE_EIF_PATH', '/opt/enclaves/executor.eif'))
    kms_proxy_port: int = field(default_factory=lambda: int(os.getenv('KMS_PROXY_PORT', '8000')))
    vsock_port: int = field(default_factory=lambda: int(os.getenv('VSOCK_PORT', '5005')))
    debug_mode: bool = field(default_factory=lambda: os.getenv('ENCLAVE_DEBUG', 'true').lower() == 'true')
    use_local_client: bool = field(default_factory=lambda: os.getenv('USE_LOCAL_ENCLAVE', 'false').lower() == 'true')


@dataclass
class RabbitMQConfig:
    """RabbitMQ configuration"""
    host: str = field(default_factory=lambda: os.getenv('RABBITMQ_HOST', 'localhost'))
    port: int = field(default_factory=lambda: int(os.getenv('RABBITMQ_PORT', '5672')))
    username: str = field(default_factory=lambda: os.getenv('RABBITMQ_USER', 'guest'))
    password: str = field(default_factory=lambda: os.getenv('RABBITMQ_PASS', 'guest'))
    execution_queue: str = field(default_factory=lambda: os.getenv('EXECUTION_QUEUE', 'job_execution'))
    result_queue: str = field(default_factory=lambda: os.getenv('RESULT_QUEUE', 'job_results'))
    prefetch_count: int = field(default_factory=lambda: int(os.getenv('RABBITMQ_PREFETCH', '1')))


@dataclass
class StorageConfig:
    """Storage configuration"""
    shared_storage_path: str = field(
        default_factory=lambda: os.getenv(
            'SHARED_STORAGE_PATH',
            '/shared/epsilon'
        )
    )
    artifacts_retention_days: int = field(default_factory=lambda: int(os.getenv('ARTIFACTS_RETENTION_DAYS', '7')))
    max_file_size_mb: int = field(default_factory=lambda: int(os.getenv('MAX_FILE_SIZE_MB', '100')))


@dataclass
class PollingConfig:
    """Polling configuration for job acquisition"""
    interval_seconds: int = field(default_factory=lambda: int(os.getenv('POLLING_INTERVAL', '5')))
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv('POLLING_TIMEOUT_SECONDS', '30')))
    max_retries: int = field(default_factory=lambda: int(os.getenv('POLLING_MAX_RETRIES', '3')))


@dataclass
class CoordinatorConfig:
    """Coordinator API configuration"""
    base_url: str = field(default_factory=lambda: os.getenv('COORDINATOR_BASE_URL', 'http://api-server:8001'))
    api_key: str = field(default_factory=lambda: os.getenv('COORDINATOR_API_KEY', ''))
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv('COORDINATOR_TIMEOUT_SECONDS', '30')))


@dataclass
class ExecutionConfig:
    """Execution-specific configuration"""
    script_timeout_seconds: int = field(default_factory=lambda: int(os.getenv('SCRIPT_TIMEOUT', '300')))
    max_output_size_mb: int = field(default_factory=lambda: int(os.getenv('MAX_OUTPUT_SIZE_MB', '10')))
    allowed_script_extensions: list = field(default_factory=lambda: ['.py'])
    python_path: str = field(default_factory=lambda: os.getenv('PYTHON_PATH', 'python3'))


@dataclass
class LoggingConfig:
    """Logging configuration"""
    level: str = field(default_factory=lambda: os.getenv('LOG_LEVEL', 'INFO'))
    format: str = field(default_factory=lambda: os.getenv(
        'LOG_FORMAT',
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    file_path: Optional[str] = field(default_factory=lambda: os.getenv('LOG_FILE'))
    max_file_size_mb: int = field(default_factory=lambda: int(os.getenv('LOG_MAX_SIZE_MB', '100')))
    backup_count: int = field(default_factory=lambda: int(os.getenv('LOG_BACKUP_COUNT', '5')))


@dataclass
class Settings:
    """Main settings container"""
    aws: AWSConfig = field(default_factory=AWSConfig)
    enclave: EnclaveConfig = field(default_factory=EnclaveConfig)
    rabbitmq: RabbitMQConfig = field(default_factory=RabbitMQConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    coordinator: CoordinatorConfig = field(default_factory=CoordinatorConfig)
    
    # Runtime settings
    worker_id: str = field(default_factory=lambda: os.getenv('WORKER_ID', 'executor-001'))
    environment: str = field(default_factory=lambda: os.getenv('ENVIRONMENT', 'development'))
    job_fetch_mode: str = field(default_factory=lambda: os.getenv('JOB_FETCH_MODE', 'polling'))  # rabbitmq, polling, or hybrid
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert settings to dictionary"""
        return {
            'aws': self.aws.__dict__,
            'enclave': self.enclave.__dict__,
            'rabbitmq': self.rabbitmq.__dict__,
            'storage': self.storage.__dict__,
            'execution': self.execution.__dict__,
            'logging': self.logging.__dict__,
            'worker_id': self.worker_id,
            'environment': self.environment
        }
    
    def is_production(self) -> bool:
        """Check if running in production"""
        return self.environment.lower() in ['production', 'prod']
    
    def is_development(self) -> bool:
        """Check if running in development"""
        return self.environment.lower() in ['development', 'dev']


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance (singleton pattern)"""
    return Settings()