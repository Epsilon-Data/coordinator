"""
Utilities for executor worker: logging and decorators.
"""
import functools
import logging
import logging.handlers
import signal
import sys
import time
from pathlib import Path
from typing import Callable, Any, Optional, Type, Tuple


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
    # Extract module name from full path (e.g., 'workers.executor.clients' -> 'clients')
    if name.startswith('workers.executor.'):
        short_name = name.replace('workers.executor.', '')
    elif '.' in name:
        short_name = name.split('.')[-1]
    else:
        short_name = name

    return logging.getLogger(f"epsilon.executor.{short_name}")


class LogContext:
    """Context manager for temporary log level changes."""

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


# Decorators
def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """Retry decorator with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            attempt = 1
            current_delay = delay
            logger = get_logger(__name__)

            while attempt <= max_attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise

                    logger.warning(
                        f"{func.__name__} attempt {attempt} failed: {e}. "
                        f"Retrying in {current_delay}s..."
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff
                    attempt += 1

        return wrapper
    return decorator


def timeout(seconds: int):
    """Timeout decorator using signals (Unix only, main thread only)."""
    def decorator(func: Callable) -> Callable:
        def handler(signum, frame):
            raise TimeoutError(f"{func.__name__} timed out after {seconds} seconds")

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            old_handler = signal.signal(signal.SIGALRM, handler)
            signal.alarm(seconds)

            try:
                result = func(*args, **kwargs)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

            return result

        return wrapper
    return decorator


def measure_time(func: Callable) -> Callable:
    """Decorator to measure function execution time."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        logger = get_logger(__name__)
        start_time = time.time()

        try:
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time
            logger.info(f"{func.__name__} completed in {execution_time:.2f} seconds")
            return result
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"{func.__name__} failed after {execution_time:.2f} seconds: {e}")
            raise

    return wrapper


def handle_exceptions(
    default_return: Any = None,
    log_errors: bool = True,
    reraise: bool = False
):
    """Decorator to handle exceptions gracefully."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            logger = get_logger(__name__)
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_errors:
                    logger.error(f"Exception in {func.__name__}: {e}", exc_info=True)

                if reraise:
                    raise

                return default_return

        return wrapper
    return decorator
