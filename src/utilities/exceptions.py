"""
Custom exceptions for Migrator.

Provides a clean exception hierarchy for migration operations.
"""


class MigratorError(Exception):
    """Base exception for all migrator errors."""

    def __init__(self, message: str, details: str | None = None):
        self.message = message
        self.details = details
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        if self.details:
            return f"{self.message}\nDetails: {self.details}"
        return self.message


class ConfigurationError(MigratorError):
    """Raised when configuration is invalid."""

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(f"Configuration error for '{field}': {message}")


class RegistryConnectionError(MigratorError):
    """Raised when registry connection fails."""

    def __init__(self, registry: str, message: str, details: str | None = None):
        self.registry = registry
        super().__init__(f"Failed to connect to {registry}: {message}", details)


class RegistryAuthenticationError(MigratorError):
    """Raised when registry authentication fails."""

    def __init__(self, registry: str, message: str = "Authentication failed"):
        self.registry = registry
        super().__init__(f"Authentication failed for {registry}: {message}")


class ImageMigrationError(MigratorError):
    """Raised when container image migration fails."""

    def __init__(self, image: str, message: str, details: str | None = None):
        self.image = image
        super().__init__(f"Failed to migrate image {image}: {message}", details)


class ChartMigrationError(MigratorError):
    """Raised when Helm chart migration fails."""

    def __init__(self, chart: str, message: str, details: str | None = None):
        self.chart = chart
        super().__init__(f"Failed to migrate chart {chart}: {message}", details)


class RetryExhaustedError(MigratorError):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, operation: str, max_attempts: int, last_error: Exception | None = None):
        self.operation = operation
        self.max_attempts = max_attempts
        self.last_error = last_error
        message = f"Operation '{operation}' failed after {max_attempts} attempts"
        details = str(last_error) if last_error else None
        super().__init__(message, details)


class ImageTransferError(MigratorError):
    """Raised when image transfer fails."""

    def __init__(self, image: str, message: str, details: str | None = None):
        self.image = image
        super().__init__(f"Failed to transfer {image}: {message}", details)


class TransientRegistryError(MigratorError):
    """Raised for transient registry errors that should be retried (5xx errors)."""

    def __init__(self, operation: str, status_code: int, details: str | None = None):
        self.status_code = status_code
        super().__init__(f"{operation} failed with status {status_code}", details)
