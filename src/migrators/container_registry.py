"""
Container Registry Migrator.

Implements efficient migration of container images and Helm charts
between any OCI-compliant registries.
"""
import asyncio
import re
import time
from typing import Any

from config import (
    ChartReference,
    ImageReference,
    MigrationConfig,
    MigrationResult,
    MigrationSummary,
)
from console import MigrationConsole
from migrators import BaseMigrator
from services.helm_client import HelmChartError, HelmClient
from services.registry_client import ImageTransferError, RegistryClient, RegistryConnectionError
from utilities.logger import get_logger

logger = get_logger(__name__)


class ContainerRegistryMigrator(BaseMigrator):
    """
    Migrates container images and Helm charts between registries.

    Features:
    - Direct registry-to-registry transfer (no local Docker daemon)
    - Parallel image migration for speed
    - Helm chart OCI migration
    - Pattern-based filtering
    - Skip existing images option
    - Detailed progress tracking
    """

    def __init__(self, config: MigrationConfig, console: MigrationConsole):
        super().__init__(config, console)
        self._source_client: RegistryClient | None = None
        self._dest_client: RegistryClient | None = None
        self._source_helm: HelmClient | None = None
        self._dest_helm: HelmClient | None = None
        self._images: list[ImageReference] = []
        self._charts: list[ChartReference] = []

    @property
    def name(self) -> str:
        return "Container Registry"

    @property
    def description(self) -> str:
        return "Migrate container images and Helm charts between OCI registries"

    async def validate_connection(self) -> bool:
        """Validate connectivity to both registries."""
        self.console.show_info("Validating registry connections...")

        # Initialize clients
        self._source_client = RegistryClient(self.config.source)
        self._dest_client = RegistryClient(self.config.destination)

        await self._source_client.connect()
        await self._dest_client.connect()

        # Check connectivity
        try:
            await self._source_client.check_connectivity()
            logger.info(f"Source registry connected: {self.config.source.registry}")
        except RegistryConnectionError as e:
            self.console.show_error("Source Registry Error", str(e))
            return False

        try:
            await self._dest_client.check_connectivity()
            logger.info(f"Destination registry connected: {self.config.destination.registry}")
        except RegistryConnectionError as e:
            self.console.show_error("Destination Registry Error", str(e))
            return False

        return True

    def _matches_filter(self, repository: str) -> bool:
        """Check if repository matches include/exclude patterns."""
        # Check exclude pattern first
        if self.config.exclude_pattern:
            if re.search(self.config.exclude_pattern, repository):
                logger.debug(f"Repository excluded by pattern: {repository}")
                return False

        # Check include pattern
        if self.config.include_pattern:
            if not re.search(self.config.include_pattern, repository):
                logger.debug(f"Repository not included by pattern: {repository}")
                return False

        return True

    async def discover(self) -> list[Any]:
        """Discover all images and charts to migrate."""
        self.console.start_discovery()

        discovered = []

        # Discover images
        with self.console.status("Fetching repository list..."):
            repositories = await self._source_client.list_repositories()

        # Filter repositories
        filtered_repos = [r for r in repositories if self._matches_filter(r)]
        logger.info(f"Found {len(filtered_repos)} repositories (filtered from {len(repositories)})")

        # Get tags for each repository
        for repo in filtered_repos:
            with self.console.status(f"Scanning {repo}..."):
                tags = await self._source_client.list_tags(repo)
                for tag in tags:
                    image_ref = ImageReference(repository=repo, tag=tag)
                    self._images.append(image_ref)
                    discovered.append(("image", image_ref))

        self.console.show_discovery_results(len(self._images), len(self._charts))

        return discovered

    async def migrate_item(self, item: Any) -> MigrationResult:
        """Migrate a single item (image or chart)."""
        item_type, ref = item

        if item_type == "image":
            return await self._migrate_image(ref)
        elif item_type == "chart":
            return await self._migrate_chart(ref)
        else:
            return MigrationResult(
                source=str(ref),
                destination="",
                success=False,
                error=f"Unknown item type: {item_type}"
            )

    async def _migrate_image(self, image: ImageReference) -> MigrationResult:
        """Migrate a single container image."""
        source_ref = image.with_registry(self.config.source.registry)
        dest_ref = image.with_registry(self.config.destination.registry)
        start_time = time.time()

        try:
            # Check if image exists in destination (skip if configured)
            if self.config.skip_existing:
                try:
                    manifest, _ = await self._dest_client.get_manifest(
                        image.repository, image.tag
                    )
                    if manifest:
                        logger.debug(f"Image already exists, skipping: {dest_ref}")
                        self.console.show_item_skipped(image.full_reference)
                        return MigrationResult(
                            source=source_ref,
                            destination=dest_ref,
                            success=True,
                            skipped=True,
                            duration_seconds=time.time() - start_time,
                        )
                except ImageTransferError:
                    pass  # Image doesn't exist, proceed with migration

            # Perform the copy (dry run just skips)
            if self.config.dry_run:
                logger.info(f"[DRY RUN] Would migrate: {source_ref} -> {dest_ref}")
                self.console.show_item_success(image.full_reference)
                return MigrationResult(
                    source=source_ref,
                    destination=dest_ref,
                    success=True,
                    duration_seconds=time.time() - start_time,
                )

            # Copy the image
            bytes_transferred = await self._source_client.copy_image(
                image.repository,
                image.tag,
                self._dest_client,
                image.repository,
                image.tag,
            )

            duration = time.time() - start_time
            size_mb = bytes_transferred / (1024 * 1024)

            self.console.show_item_success(image.full_reference, size_mb)
            logger.info(f"Migrated {source_ref} -> {dest_ref} ({size_mb:.1f} MB in {duration:.1f}s)")

            return MigrationResult(
                source=source_ref,
                destination=dest_ref,
                success=True,
                duration_seconds=duration,
                size_bytes=bytes_transferred,
            )

        except ImageTransferError as e:
            self.console.show_item_failure(image.full_reference, str(e))
            logger.error(f"Failed to migrate {source_ref}: {e}")
            return MigrationResult(
                source=source_ref,
                destination=dest_ref,
                success=False,
                error=str(e),
                duration_seconds=time.time() - start_time,
            )
        except Exception as e:
            self.console.show_item_failure(image.full_reference, str(e))
            logger.exception(f"Unexpected error migrating {source_ref}")
            return MigrationResult(
                source=source_ref,
                destination=dest_ref,
                success=False,
                error=str(e),
                duration_seconds=time.time() - start_time,
            )

    async def _migrate_chart(self, chart: ChartReference) -> MigrationResult:
        """Migrate a single Helm chart."""
        source_ref = f"{self.config.source.registry}/{chart.full_reference}"
        dest_ref = f"{self.config.destination.registry}/{chart.full_reference}"
        start_time = time.time()

        try:
            if self.config.dry_run:
                logger.info(f"[DRY RUN] Would migrate chart: {source_ref} -> {dest_ref}")
                self.console.show_item_success(chart.full_reference)
                return MigrationResult(
                    source=source_ref,
                    destination=dest_ref,
                    success=True,
                    duration_seconds=time.time() - start_time,
                )

            # Copy the chart
            bytes_transferred = await self._source_helm.copy_chart(
                chart,
                self._dest_helm,
                chart.repository,
            )

            duration = time.time() - start_time
            self.console.show_item_success(chart.full_reference)

            return MigrationResult(
                source=source_ref,
                destination=dest_ref,
                success=True,
                duration_seconds=duration,
                size_bytes=bytes_transferred,
            )

        except HelmChartError as e:
            self.console.show_item_failure(chart.full_reference, str(e))
            return MigrationResult(
                source=source_ref,
                destination=dest_ref,
                success=False,
                error=str(e),
                duration_seconds=time.time() - start_time,
            )
        except Exception as e:
            self.console.show_item_failure(chart.full_reference, str(e))
            return MigrationResult(
                source=source_ref,
                destination=dest_ref,
                success=False,
                error=str(e),
                duration_seconds=time.time() - start_time,
            )

    async def _migrate_batch(
        self,
        items: list[tuple[str, Any]],
        progress_task
    ) -> list[MigrationResult]:
        """Migrate a batch of items with parallelism."""
        semaphore = asyncio.Semaphore(self.config.parallel_jobs)

        async def migrate_with_semaphore(item):
            async with semaphore:
                result = await self.migrate_item(item)
                self.add_result(result)
                progress_task.advance(1)
                return result

        tasks = [migrate_with_semaphore(item) for item in items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any exceptions that weren't caught
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                item_type, ref = items[i]
                final_results.append(MigrationResult(
                    source=str(ref),
                    destination="",
                    success=False,
                    error=str(result),
                ))
            else:
                final_results.append(result)

        return final_results

    async def run(self) -> MigrationSummary:
        """Execute the complete migration."""
        start_time = time.time()

        self.log_start()
        self.console.print_banner()
        self.console.print_config_summary(
            self.config.source.registry,
            self.config.destination.registry,
            self.config.parallel_jobs,
            self.config.dry_run,
        )

        try:
            # Validate connections
            if not await self.validate_connection():
                raise RegistryConnectionError(
                    "registries",
                    "Failed to connect to one or more registries"
                )

            # Discover items
            items = await self.discover()

            if not items:
                self.console.show_warning("No items found to migrate")
                return self.finalize_summary(start_time)

            # Confirm migration (unless dry run)
            if not self.config.dry_run:
                total = len(items)
                if total > 10 and not self.console.confirm(
                    f"Migrate {total} items? This may take a while."
                ):
                    self.console.show_info("Migration cancelled by user")
                    return self.finalize_summary(start_time)

            # Run migration with progress tracking
            with self.console.migration_progress(len(items)) as progress:
                task = progress.add_task(
                    "Migrating...",
                    total=len(items)
                )

                # Create a simple progress adapter
                class ProgressAdapter:
                    def __init__(self, p, t):
                        self._progress = p
                        self._task = t

                    def advance(self, n=1):
                        self._progress.update(self._task, advance=n)

                await self._migrate_batch(items, ProgressAdapter(progress, task))

            # Show summary
            summary = self.finalize_summary(start_time)
            self.console.show_summary(summary)

            if self.config.dry_run:
                self.console.show_dry_run_notice()

            self.log_complete(summary)
            return summary

        except Exception as e:
            logger.exception("Migration failed")
            self.console.show_error("Migration Failed", str(e))
            return self.finalize_summary(start_time)

        finally:
            # Cleanup
            if self._source_client:
                await self._source_client.close()
            if self._dest_client:
                await self._dest_client.close()
            if self._source_helm:
                await self._source_helm.cleanup()
            if self._dest_helm:
                await self._dest_helm.cleanup()
