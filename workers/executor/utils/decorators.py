"""
Useful decorators for the Executor
"""
import functools
import time
import signal
from typing import Callable, Any, Optional, Type, Tuple
from exceptions import TimeoutError, ExecutorError
from utils.logging import get_logger

logger = get_logger(__name__)


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    Retry decorator with exponential backoff
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Backoff multiplier
        exceptions: Tuple of exceptions to catch
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            attempt = 1
            current_delay = delay
            
            while attempt <= max_attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
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
    """
    Timeout decorator using signals
    
    Args:
        seconds: Timeout in seconds
        
    Note:
        This only works on Unix-like systems and in the main thread
    """
    def decorator(func: Callable) -> Callable:
        def handler(signum, frame):
            raise TimeoutError(
                f"{func.__name__} timed out after {seconds} seconds",
                timeout_seconds=seconds
            )
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Set the alarm
            old_handler = signal.signal(signal.SIGALRM, handler)
            signal.alarm(seconds)
            
            try:
                result = func(*args, **kwargs)
            finally:
                # Cancel the alarm and restore the old handler
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            
            return result
        
        return wrapper
    return decorator


def measure_time(func: Callable) -> Callable:
    """
    Decorator to measure function execution time
    
    Logs the execution time at INFO level
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        start_time = time.time()
        
        try:
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time
            logger.info(
                f"{func.__name__} completed in {execution_time:.2f} seconds"
            )
            return result
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(
                f"{func.__name__} failed after {execution_time:.2f} seconds: {e}"
            )
            raise
    
    return wrapper


def handle_exceptions(
    default_return: Any = None,
    log_errors: bool = True,
    reraise: bool = False
):
    """
    Decorator to handle exceptions gracefully
    
    Args:
        default_return: Value to return on exception
        log_errors: Whether to log errors
        reraise: Whether to re-raise the exception
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_errors:
                    logger.error(
                        f"Exception in {func.__name__}: {e}",
                        exc_info=True
                    )
                
                if reraise:
                    raise
                
                return default_return
        
        return wrapper
    return decorator


def validate_input(**validators):
    """
    Decorator to validate function inputs
    
    Args:
        **validators: Keyword arguments mapping parameter names to validator functions
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Get function signature
            import inspect
            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            
            # Validate each parameter
            for param_name, validator in validators.items():
                if param_name in bound.arguments:
                    value = bound.arguments[param_name]
                    try:
                        if not validator(value):
                            raise ValueError(
                                f"Validation failed for parameter '{param_name}'"
                            )
                    except Exception as e:
                        logger.error(f"Input validation error: {e}")
                        raise
            
            return func(*args, **kwargs)
        
        return wrapper
    return decorator