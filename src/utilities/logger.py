"""
Logging configuration for Migrator.

Provides standardized logging setup with Rich integration support.
"""
import logging
import threading

_lock = threading.Lock()
_handlers_configured: set[str] = set()
_global_level: int = logging.WARNING


def get_logger(name: str = __name__, level: int | None = None) -> logging.Logger:
    """
    Get a configured logger instance.

    Args:
        name: Logger name (typically __name__)
        level: Optional log level override

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    effective_level = level if level is not None else _global_level
    logger.setLevel(effective_level)
    
    # Disable propagation to prevent duplicate log entries
    logger.propagate = False

    with _lock:
        if name not in _handlers_configured:
            fmt = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            ch = logging.StreamHandler()
            ch.setLevel(effective_level)
            ch.setFormatter(fmt)
            logger.addHandler(ch)
            _handlers_configured.add(name)

    return logger


def set_global_level(level: int) -> None:
    """
    Set the global default log level.

    Args:
        level: Log level (e.g., logging.DEBUG, logging.INFO)
    """
    global _global_level
    _global_level = level

    # Update existing loggers
    for name in _handlers_configured:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        for handler in logger.handlers:
            handler.setLevel(level)
