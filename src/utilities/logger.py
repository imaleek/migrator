"""
Logging configuration for Migrator.

Provides standardized logging setup with Rich integration for
coordinated output with progress bars and live displays.
Supports file logging for separating verbose logs from console output.
"""
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.logging import RichHandler

if TYPE_CHECKING:
    pass

_lock = threading.Lock()
_handlers_configured: set[str] = set()
_global_level: int = logging.WARNING
_shared_console: Console | None = None
_log_file_path: Path | None = None
_file_handler: logging.Handler | None = None


def set_console(console: Console) -> None:
    """
    Set the shared Rich console for log output.
    
    This allows logs to coordinate with progress bars and live displays.
    
    Args:
        console: Rich Console instance to use for logging
    """
    global _shared_console
    _shared_console = console


def get_console() -> Console:
    """Get the shared console, creating one if needed."""
    global _shared_console
    if _shared_console is None:
        _shared_console = Console()
    return _shared_console


def set_log_file(log_file: Path | str | None) -> None:
    """
    Set a file for logging output.
    
    When set, DEBUG/INFO logs go to file while console shows only WARNING+.
    This keeps the console clean for progress bars.
    
    Args:
        log_file: Path to log file, or None to disable file logging
    """
    global _log_file_path, _file_handler
    
    if log_file is None:
        _log_file_path = None
        return
    
    _log_file_path = Path(log_file)
    
    # Create file handler
    _file_handler = logging.FileHandler(_log_file_path, mode='a', encoding='utf-8')
    _file_handler.setLevel(logging.DEBUG)
    
    # Use standard formatter for file (no Rich markup)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    _file_handler.setFormatter(fmt)
    
    # Add to all configured loggers
    with _lock:
        for name in _handlers_configured:
            logger = logging.getLogger(name)
            logger.addHandler(_file_handler)


def get_logger(name: str = __name__, level: int | None = None) -> logging.Logger:
    """
    Get a configured logger instance with Rich integration.

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
            # Use RichHandler for console output
            # When file logging is enabled, console only shows WARNING+
            console_level = logging.WARNING if _log_file_path else effective_level
            
            handler = RichHandler(
                console=get_console(),
                show_time=True,
                show_level=True,
                show_path=False,
                rich_tracebacks=True,
                tracebacks_show_locals=False,
                markup=True,
                log_time_format="%Y-%m-%d %H:%M:%S",
            )
            handler.setLevel(console_level)
            
            # Custom format - RichHandler handles time/level
            fmt = logging.Formatter("%(name)s | %(message)s")
            handler.setFormatter(fmt)
            
            logger.addHandler(handler)
            
            # Add file handler if configured
            if _file_handler:
                logger.addHandler(_file_handler)
            
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
            # Don't change file handler level (always accepts all)
            if isinstance(handler, RichHandler):
                # When file logging enabled, console stays at WARNING
                if _log_file_path:
                    handler.setLevel(logging.WARNING)
                else:
                    handler.setLevel(level)
            elif not isinstance(handler, logging.FileHandler):
                handler.setLevel(level)
