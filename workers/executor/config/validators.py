"""
Configuration validators to ensure environment is properly set up
"""
import os
from typing import List, Tuple
from pathlib import Path

from config.settings import Settings


class ConfigurationError(Exception):
    """Raised when configuration validation fails"""
    pass


def validate_environment(settings: Settings) -> Tuple[bool, List[str]]:
    """
    Validate the environment configuration
    
    Args:
        settings: Settings instance to validate
        
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    # Validate AWS configuration
    if not settings.aws.kms_key_arn and not settings.enclave.use_local_client:
        errors.append("AWS_KMS_KEY_ARN is required when not using local client")
    
    if not settings.aws.access_key_id:
        errors.append("AWS_ACCESS_KEY_ID is required")
    
    if not settings.aws.secret_access_key:
        errors.append("AWS_SECRET_ACCESS_KEY is required")
    
    # Validate Enclave configuration
    if not settings.enclave.use_local_client:
        eif_path = Path(settings.enclave.eif_path)
        if not eif_path.exists():
            errors.append(f"Enclave EIF file not found: {settings.enclave.eif_path}")
    
    # Validate storage paths
    storage_path = Path(settings.storage.shared_storage_path)
    if not storage_path.exists():
        errors.append(f"Shared storage path does not exist: {settings.storage.shared_storage_path}")
    
    # Validate execution limits
    if settings.execution.script_timeout_seconds < 1:
        errors.append("Script timeout must be at least 1 second")
    
    if settings.execution.max_output_size_mb < 1:
        errors.append("Max output size must be at least 1 MB")
    
    # Production-specific validations
    if settings.is_production():
        if settings.enclave.debug_mode:
            errors.append("Debug mode should be disabled in production")
        
        if settings.logging.level == 'DEBUG':
            errors.append("Debug logging should not be used in production")
    
    return len(errors) == 0, errors


def check_permissions(settings: Settings) -> Tuple[bool, List[str]]:
    """
    Check file system permissions
    
    Args:
        settings: Settings instance
        
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []
    
    # Check storage directory is writable
    storage_path = Path(settings.storage.shared_storage_path)
    if storage_path.exists() and not os.access(storage_path, os.W_OK):
        errors.append(f"Storage path is not writable: {storage_path}")
    
    # Check log file directory is writable if specified
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
    """
    Validate configuration and raise exception if invalid
    
    Args:
        settings: Settings instance to validate
        
    Raises:
        ConfigurationError: If validation fails
    """
    # Run all validations
    env_valid, env_errors = validate_environment(settings)
    perm_valid, perm_errors = check_permissions(settings)
    
    all_errors = env_errors + perm_errors
    
    if not (env_valid and perm_valid):
        error_msg = "Configuration validation failed:\n"
        error_msg += "\n".join(f"  - {error}" for error in all_errors)
        raise ConfigurationError(error_msg)