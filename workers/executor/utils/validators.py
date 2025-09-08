"""
Input validation utilities
"""
import os
from pathlib import Path
from typing import Optional, List
import re

from config import get_settings
from exceptions import ValidationError


def validate_file_path(
    file_path: str,
    must_exist: bool = True,
    allowed_extensions: Optional[List[str]] = None,
    base_path: Optional[str] = None
) -> bool:
    """
    Validate a file path
    
    Args:
        file_path: Path to validate
        must_exist: Whether the file must exist
        allowed_extensions: List of allowed file extensions
        base_path: Base path for security validation
        
    Returns:
        True if valid
        
    Raises:
        ValidationError: If validation fails
    """
    if not file_path:
        raise ValidationError("File path cannot be empty")
    
    path = Path(file_path)
    
    # Security check - prevent path traversal
    if base_path:
        try:
            # Resolve to absolute paths
            base = Path(base_path).resolve()
            target = path.resolve()
            
            # Check if target is under base
            target.relative_to(base)
        except ValueError:
            raise ValidationError(
                f"Path '{file_path}' is outside allowed base path",
                field='file_path'
            )
    
    # Check existence
    if must_exist and not path.exists():
        raise ValidationError(
            f"File not found: {file_path}",
            field='file_path'
        )
    
    # Check extension
    if allowed_extensions and path.suffix not in allowed_extensions:
        raise ValidationError(
            f"File extension '{path.suffix}' not allowed. "
            f"Allowed: {', '.join(allowed_extensions)}",
            field='file_path'
        )
    
    return True


def validate_script_content(content: str, max_size_mb: Optional[float] = None) -> bool:
    """
    Validate script content
    
    Args:
        content: Script content to validate
        max_size_mb: Maximum size in megabytes
        
    Returns:
        True if valid
        
    Raises:
        ValidationError: If validation fails
    """
    if not content:
        raise ValidationError("Script content cannot be empty")
    
    # Check size
    if max_size_mb:
        size_mb = len(content.encode('utf-8')) / (1024 * 1024)
        if size_mb > max_size_mb:
            raise ValidationError(
                f"Script size ({size_mb:.2f} MB) exceeds limit ({max_size_mb} MB)"
            )
    
    # Check for potentially dangerous imports/calls
    dangerous_patterns = [
        r'__import__',
        r'exec\s*\(',
        r'eval\s*\(',
        r'compile\s*\(',
        r'os\.system',
        r'subprocess\.call',
        r'subprocess\.run',
        r'subprocess\.Popen',
    ]
    
    settings = get_settings()
    if settings.is_production():
        for pattern in dangerous_patterns:
            if re.search(pattern, content):
                raise ValidationError(
                    f"Script contains potentially dangerous code: {pattern}"
                )
    
    return True


def validate_job_id(job_id: str) -> bool:
    """
    Validate job ID format
    
    Args:
        job_id: Job ID to validate
        
    Returns:
        True if valid
        
    Raises:
        ValidationError: If validation fails
    """
    if not job_id:
        raise ValidationError("Job ID cannot be empty")
    
    # Check format (alphanumeric with hyphens)
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9]$', job_id):
        raise ValidationError(
            "Job ID must be alphanumeric with optional hyphens",
            field='job_id'
        )
    
    # Check length
    if len(job_id) > 64:
        raise ValidationError(
            "Job ID must not exceed 64 characters",
            field='job_id'
        )
    
    return True


def validate_repository_path(repo_path: str) -> bool:
    """
    Validate repository path
    
    Args:
        repo_path: Repository path to validate
        
    Returns:
        True if valid
        
    Raises:
        ValidationError: If validation fails
    """
    settings = get_settings()
    base_path = settings.storage.shared_storage_path
    
    # Use file path validator with repository-specific rules
    return validate_file_path(
        repo_path,
        must_exist=True,
        base_path=base_path
    )