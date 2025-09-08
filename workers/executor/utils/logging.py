"""
Logging configuration and utilities
"""
import logging
import logging.handlers
import sys
from typing import Optional
from pathlib import Path

from config import get_settings


class ContextFilter(logging.Filter):
    """Add context information to log records"""
    
    def __init__(self, worker_id: str, environment: str):
        super().__init__()
        self.worker_id = worker_id
        self.environment = environment
    
    def filter(self, record):
        record.worker_id = self.worker_id
        record.environment = self.environment
        return True


def setup_logging(
    name: Optional[str] = None,
    level: Optional[str] = None,
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Set up logging configuration
    
    Args:
        name: Logger name (defaults to root)
        level: Log level (defaults to settings)
        log_file: Log file path (defaults to settings)
        
    Returns:
        Configured logger instance
    """
    settings = get_settings()
    
    # Use provided values or fall back to settings
    log_level = level or settings.logging.level
    file_path = log_file or settings.logging.file_path
    
    # Get or create logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Create formatter
    formatter = logging.Formatter(
        settings.logging.format,
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Add context filter
    context_filter = ContextFilter(
        worker_id=settings.worker_id,
        environment=settings.environment
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)
    logger.addHandler(console_handler)
    
    # File handler if specified
    if file_path:
        try:
            # Ensure directory exists
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Create rotating file handler
            file_handler = logging.handlers.RotatingFileHandler(
                file_path,
                maxBytes=settings.logging.max_file_size_mb * 1024 * 1024,
                backupCount=settings.logging.backup_count
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(context_filter)
            logger.addHandler(file_handler)
        except Exception as e:
            logger.error(f"Failed to set up file logging: {e}")
    
    # Log initial message
    logger.info(
        f"Logger initialized - Worker: {settings.worker_id}, "
        f"Environment: {settings.environment}, Level: {log_level}"
    )
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the module name
    
    Args:
        name: Logger name (usually __name__)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class LogContext:
    """Context manager for temporary log level changes"""
    
    def __init__(self, logger: logging.Logger, level: str):
        self.logger = logger
        self.new_level = getattr(logging, level.upper())
        self.original_level = None
    
    def __enter__(self):
        self.original_level = self.logger.level
        self.logger.setLevel(self.new_level)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.logger.setLevel(self.original_level)