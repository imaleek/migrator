"""
Base migrator class and common structures.

Provides abstract base class for all migration types.
"""
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from config import MigrationConfig, MigrationResult, MigrationSummary
from console import MigrationConsole
from utilities.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MigrationContext:
    """Context passed to migration operations."""
    config: MigrationConfig
    console: MigrationConsole
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed time since migration started."""
        return time.time() - self.start_time


class BaseMigrator(ABC):
    """
    Abstract base class for all migrators.

    Provides common functionality:
    - Configuration handling
    - Progress tracking integration
    - Logging hooks
    - Summary generation
    """

    def __init__(self, config: MigrationConfig, console: MigrationConsole):
        """
        Initialize the migrator.

        Args:
            config: Migration configuration
            console: Console UI for progress display
        """
        self.config = config
        self.console = console
        self._summary = MigrationSummary()

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the migrator."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what this migrator does."""
        pass

    @abstractmethod
    async def validate_connection(self) -> bool:
        """
        Validate connectivity to source and destination.

        Returns:
            True if both connections are valid

        Raises:
            RegistryConnectionError: If connection fails
        """
        pass

    @abstractmethod
    async def discover(self) -> list[Any]:
        """
        Discover items to migrate from source.

        Returns:
            List of items to migrate
        """
        pass

    @abstractmethod
    async def migrate_item(self, item: Any) -> MigrationResult:
        """
        Migrate a single item.

        Args:
            item: Item to migrate

        Returns:
            Result of the migration
        """
        pass

    @abstractmethod
    async def run(self) -> MigrationSummary:
        """
        Execute the complete migration.

        Returns:
            Summary of the migration operation
        """
        pass

    def add_result(self, result: MigrationResult) -> None:
        """Add a migration result to the summary."""
        self._summary.results.append(result)
        self._summary.total_items += 1

        if result.skipped:
            self._summary.skipped += 1
        elif result.success:
            self._summary.successful += 1
            if result.size_bytes:
                self._summary.total_size_bytes += result.size_bytes
        else:
            self._summary.failed += 1

    def finalize_summary(self, start_time: float) -> MigrationSummary:
        """Finalize the migration summary with timing info."""
        self._summary.total_duration_seconds = time.time() - start_time
        return self._summary

    def log_start(self) -> None:
        """Log migration start."""
        logger.info(f"Starting {self.name} migration")
        logger.info(f"Source: {self.config.source.registry}")
        logger.info(f"Destination: {self.config.destination.registry}")

    def log_complete(self, summary: MigrationSummary) -> None:
        """Log migration completion."""
        logger.info(
            f"Migration complete: {summary.successful} succeeded, "
            f"{summary.failed} failed, {summary.skipped} skipped"
        )
