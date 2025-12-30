"""
Utility decorators for Migrator.

Provides retry and logging decorators for robust operations.
"""
import functools
import time
from collections.abc import Callable
from typing import TypeVar

from utilities.logger import get_logger

logger = get_logger(__name__)

F = TypeVar('F', bound=Callable)


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable[[int, Exception], None] | None = None
) -> Callable[[F], F]:
    """
    Decorator to retry a function on failure with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts
        delay: Initial delay between retries (seconds)
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exceptions to catch and retry
        on_retry: Optional callback called on each retry

    Returns:
        Decorated function
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        logger.warning(
                            f"Attempt {attempt}/{max_attempts} failed for {func.__name__}: {e}. "
                            f"Retrying in {current_delay:.1f}s..."
                        )
                        if on_retry:
                            on_retry(attempt, e)
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"All {max_attempts} attempts failed for {func.__name__}: {e}"
                        )

            raise last_exception

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            import asyncio
            current_delay = delay
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        logger.warning(
                            f"Attempt {attempt}/{max_attempts} failed for {func.__name__}: {e}. "
                            f"Retrying in {current_delay:.1f}s..."
                        )
                        if on_retry:
                            on_retry(attempt, e)
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"All {max_attempts} attempts failed for {func.__name__}: {e}"
                        )

            raise last_exception

        # Return appropriate wrapper based on function type
        if asyncio_iscoroutinefunction(func):
            return async_wrapper
        return wrapper

    return decorator


def asyncio_iscoroutinefunction(func: Callable) -> bool:
    """Check if function is a coroutine function."""
    import asyncio
    return asyncio.iscoroutinefunction(func)


def log_operation(operation_name: str | None = None) -> Callable[[F], F]:
    """
    Decorator to log the start and end of an operation.

    Args:
        operation_name: Name to log (defaults to function name)

    Returns:
        Decorated function
    """
    def decorator(func: F) -> F:
        name = operation_name or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger.debug(f"Starting: {name}")
            try:
                result = func(*args, **kwargs)
                logger.debug(f"Completed: {name}")
                return result
            except Exception as e:
                logger.error(f"Failed: {name} - {e}")
                raise

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger.debug(f"Starting: {name}")
            try:
                result = await func(*args, **kwargs)
                logger.debug(f"Completed: {name}")
                return result
            except Exception as e:
                logger.error(f"Failed: {name} - {e}")
                raise

        if asyncio_iscoroutinefunction(func):
            return async_wrapper
        return wrapper

    return decorator
