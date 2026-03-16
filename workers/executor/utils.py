"""
Utilities for executor worker: logging configuration.
"""
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional


class ContextFilter(logging.Filter):
    """Add context information to log records."""

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
    """Set up logging configuration."""
    from workers.executor.settings import get_settings

    settings = get_settings()

    log_level = level or settings.logging.level
    file_path = log_file or settings.logging.file_path

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))
    logger.handlers.clear()

    formatter = logging.Formatter(
        settings.logging.format,
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    context_filter = ContextFilter(
        worker_id=settings.worker_id,
        environment=settings.environment
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)
    logger.addHandler(console_handler)

    if file_path:
        try:
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)

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

    logger.info(
        f"Logger initialized - Worker: {settings.worker_id}, "
        f"Environment: {settings.environment}, Level: {log_level}"
    )

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for the executor package.

    Uses hierarchical naming under 'epsilon.executor' namespace.
    Example: get_logger(__name__) returns 'epsilon.executor.clients' for clients.py
    """
    if name.startswith('workers.executor.'):
        short_name = name.replace('workers.executor.', '')
    elif '.' in name:
        short_name = name.split('.')[-1]
    else:
        short_name = name

    return logging.getLogger(f"epsilon.executor.{short_name}")
