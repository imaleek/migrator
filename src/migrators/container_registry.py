"""
Container Registry Migrator.

Implements efficient migration of container images and Helm charts
between any OCI-compliant registries.
"""
import asyncio
import re
import time
from pathlib import Path
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
from services.blob_cache import BlobCache
from services.migration_state import MigrationState
from services.registry_client import ManifestTransferError, RegistryClient, RegistryConnectionError
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
        self._images: list[ImageReference] = []
        self._charts: list[ChartReference] = []
        self._state: MigrationState | None = None
        self._blob_cache: BlobCache | None = None
        self._checkpoint_counter: int = 0
        # Per-repository tracking for accountability
        self._repo_stats: dict[str, dict] = {}  # repo -> {discovered, migrated, failed, skipped}

    @property
    def name(self) -> str:
        return "Container Registry"

    @property
    def description(self) -> str:
        return "Migrate container images and Helm charts between OCI registries"

    async def validate_connection(self) -> bool:
        """Validate connectivity to both registries."""
        self.console.show_info("Validating registry connections...")

        # Initialize registry clients
        self._source_client = RegistryClient(self.config.source)
        self._dest_client = RegistryClient(self.config.destination)

        await self._source_client.connect()
        await self._dest_client.connect()

        # Check connectivity
        try:
            await self._source_client.check_connectivity()
        except RegistryConnectionError as e:
            self.console.show_error("Source Registry Error", str(e))
            return False

        try:
            await self._dest_client.check_connectivity()
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

    def _strip_namespace(self, repository: str, namespace: str | None) -> str:
        """
        Remove namespace prefix from repository path.

        Args:
            repository: Full repository path (e.g., "project/myapp")
            namespace: Namespace to strip (e.g., "project")

        Returns:
            Repository without namespace (e.g., "myapp")
        """
        if namespace and repository.startswith(f"{namespace}/"):
            return repository[len(namespace) + 1:]
        return repository

    def _apply_namespace(self, repository: str, namespace: str | None) -> str:
        """
        Add namespace prefix to repository path.

        Args:
            repository: Repository path (e.g., "myapp")
            namespace: Namespace to add (e.g., "project")

        Returns:
            Repository with namespace (e.g., "project/myapp")
        """
        if namespace:
            # Avoid double-prefixing
            if not repository.startswith(f"{namespace}/"):
                return f"{namespace}/{repository}"
        return repository

    async def discover(self) -> list[Any]:
        """Discover all images and charts to migrate."""
        self.console.start_discovery()

        discovered = []
        source_prefix = self.config.source.repository_prefix

        # Discover images
        with self.console.status("Fetching repository list..."):
            repositories = [r async for r in self._source_client.list_repositories()]

        total_from_registry = len(repositories)

        # Filter by namespace prefix if set
        if source_prefix:
            repositories = [
                r for r in repositories
                if r.startswith(f"{source_prefix}/") or r == source_prefix
            ]
            logger.info(f"Filtered to {len(repositories)} repositories in namespace '{source_prefix}'")

        # Apply include/exclude pattern filters
        filtered_repos = [r for r in repositories if self._matches_filter(r)]
        
        if len(filtered_repos) != total_from_registry:
            logger.info(f"Scanning {len(filtered_repos)} repositories (filtered from {total_from_registry})")
        else:
            logger.info(f"Scanning {len(filtered_repos)} repositories")

        # Use config for scan concurrency (default 10, reduced from 50 to avoid overwhelming registry)
        scan_concurrency = self.config.scan_concurrency
        semaphore = asyncio.Semaphore(scan_concurrency)
        
        # Progress counter
        scanned_count = 0
        total_repos = len(filtered_repos)
        failed_count = 0

        async def scan_repository(repo: str) -> list[tuple[str, Any]]:
            """Scan a single repository for tags and classify items with retry."""
            nonlocal scanned_count, failed_count
            
            # Retry parameters for transient errors
            max_retries = 3
            base_delay = 2.0
            
            async with semaphore:
                last_error = None
                for attempt in range(max_retries):
                    try:
                        # list_tags now uses async generator
                        tags = [t async for t in self._source_client.list_tags(repo)]
                        if not tags:
                            scanned_count += 1
                            return []

                        # Initialize repo stats
                        self._repo_stats[repo] = {
                            "discovered": len(tags),
                            "migrated": 0,
                            "failed": 0,
                            "skipped": 0,
                            "is_chart": False,
                        }

                        # ALWAYS check manifest to properly detect type
                        # This is required because Helm charts stored as OCI 
                        # often don't follow naming patterns
                        is_chart = False
                        try:
                            manifest_info, manifest_bytes = await self._source_client.get_manifest(
                                repo, tags[0]
                            )
                            manifest_data = __import__("json").loads(manifest_bytes)
                            is_chart = self._source_client.is_helm_chart(manifest_data)
                        except Exception as e:
                            logger.debug(f"Could not fetch manifest for {repo}:{tags[0]}: {e}")
                        
                        self._repo_stats[repo]["is_chart"] = is_chart
                        
                        # Apply skip filters
                        # Skip Helm charts if configured
                        if is_chart and self.config.skip_charts:
                            scanned_count += 1
                            self._repo_stats[repo]["skipped"] = len(tags)
                            logger.debug(f"Skipping Helm chart repository: {repo}")
                            return []
                        
                        # Skip regular images if configured  
                        if not is_chart and self.config.skip_images:
                            scanned_count += 1
                            self._repo_stats[repo]["skipped"] = len(tags)
                            logger.debug(f"Skipping container image repository: {repo}")
                            return []

                        items = []
                        for tag in tags:
                            if is_chart:
                                # Extract chart name from repository path
                                chart_name = repo.split("/")[-1]
                                chart_ref = ChartReference(
                                    name=chart_name,
                                    version=tag,
                                    repository="/".join(repo.split("/")[:-1]) if "/" in repo else None
                                )
                                items.append(("chart", chart_ref))
                            else:
                                image_ref = ImageReference(repository=repo, tag=tag)
                                items.append(("image", image_ref))
                        
                        scanned_count += 1
                        # Log progress every 20 repositories
                        if scanned_count % 20 == 0:
                            logger.info(f"Scanned {scanned_count}/{total_repos} repositories...")
                        
                        return items
                        
                    except Exception as e:
                        last_error = e
                        error_str = str(e).lower()
                        # Check if this is a retryable error
                        is_transient = any(msg in error_str for msg in [
                            'disconnect', 'timeout', 'connection', 'reset',
                            'server error', '502', '503', '504', 'temporarily'
                        ])
                        
                        if is_transient and attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)  # Exponential backoff
                            logger.debug(f"Retry {attempt + 1}/{max_retries} for {repo} in {delay}s: {e}")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            break  # Non-transient error or max retries reached
                
                # All retries failed
                scanned_count += 1
                failed_count += 1
                # Track failed scan
                self._repo_stats[repo] = {
                    "discovered": 0,
                    "migrated": 0,
                    "failed": 0,
                    "skipped": 0,
                    "scan_error": str(last_error),
                }
                logger.warning(f"Failed to scan repository {repo}: {last_error}")
                return []

        # Scan all repositories in parallel with progress indication
        with self.console.status(f"Scanning {total_repos} repositories (concurrency: {scan_concurrency})..."):
            scan_tasks = [scan_repository(repo) for repo in filtered_repos]
            results = await asyncio.gather(*scan_tasks, return_exceptions=True)

        # Collect results with deduplication
        seen_refs: set[str] = set()
        duplicate_count = 0
        
        for result in results:
            if isinstance(result, Exception):
                logger.debug(f"Repository scan failed: {result}")
                continue
            for item_type, ref in result:
                # Create unique key for deduplication
                if item_type == "chart":
                    ref_key = f"chart:{ref.full_reference}"
                else:
                    ref_key = f"image:{ref.full_reference}"
                
                # Skip if already seen
                if ref_key in seen_refs:
                    duplicate_count += 1
                    continue
                seen_refs.add(ref_key)
                
                if item_type == "chart":
                    self._charts.append(ref)
                else:
                    self._images.append(ref)
                discovered.append((item_type, ref))

        # Log scan summary
        if duplicate_count > 0:
            logger.debug(f"Removed {duplicate_count} duplicate items during discovery")
        if failed_count > 0:
            logger.info(f"Scan complete: {len(discovered)} items found, {failed_count} repositories failed")
        else:
            logger.info(f"Scan complete: {len(discovered)} items found")

        self.console.show_discovery_results(len(self._images), len(self._charts))

        return discovered

    async def _is_helm_chart_repo(self, repository: str, tag: str) -> bool:
        """
        Check if a repository contains Helm charts by inspecting manifest.

        Args:
            repository: Repository name
            tag: A sample tag to check

        Returns:
            True if the repository contains Helm charts
        """
        try:
            manifest_info, manifest_bytes = await self._source_client.get_manifest(
                repository, tag
            )
            manifest_data = __import__("json").loads(manifest_bytes)
            return self._source_client.is_helm_chart(manifest_data)
        except Exception as e:
            logger.debug(f"Could not determine artifact type for {repository}:{tag}: {e}")
            return False

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
        # Get namespace prefixes
        source_prefix = self.config.source.repository_prefix
        dest_prefix = self.config.destination.repository_prefix

        # Source repository is as discovered (includes source namespace if present)
        source_repo = image.repository

        # For destination, strip source namespace and apply destination namespace
        # e.g., source "project-a/myapp" with dest namespace "project-b" -> "project-b/myapp"
        base_repo = self._strip_namespace(source_repo, source_prefix)
        dest_repo = self._apply_namespace(base_repo, dest_prefix)

        # Build full references for logging
        source_ref = f"{self.config.source.registry_host}/{source_repo}:{image.tag}"
        dest_ref = f"{self.config.destination.registry_host}/{dest_repo}:{image.tag}"
        start_time = time.time()

        try:
            # Check if image exists in destination (skip if configured)
            if self.config.skip_existing and self._dest_client:
                try:
                    manifest, _ = await self._dest_client.get_manifest(
                        dest_repo, image.tag
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
                except ManifestTransferError:
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

            # Copy the image with layer concurrency and blob cache
            bytes_transferred = await self._source_client.copy_image(
                source_repo,
                image.tag,
                self._dest_client,
                dest_repo,
                image.tag,
                layer_concurrency=self.config.layer_concurrency,
                blob_cache=self._blob_cache,
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

        except ManifestTransferError as e:
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
        source_prefix = self.config.source.repository_prefix
        dest_prefix = self.config.destination.repository_prefix

        # Determine source and destination repositories
        if chart.repository:
            source_repo = f"{chart.repository}/{chart.name}"
        else:
            source_repo = chart.name
            
        base_repo = self._strip_namespace(source_repo, source_prefix)
        dest_repo = self._apply_namespace(base_repo, dest_prefix)

        # Build full references for logging
        source_ref = f"{self.config.source.registry}/{source_repo}:{chart.version}"
        dest_ref = f"{self.config.destination.registry}/{dest_repo}:{chart.version}"
        
        start_time = time.time()

        try:
            if self.config.skip_existing and self._dest_client:
                try:
                    manifest, _ = await self._dest_client.get_manifest(
                        dest_repo, chart.version
                    )
                    if manifest:
                        logger.debug(f"Chart already exists, skipping: {dest_ref}")
                        self.console.show_item_skipped(chart.full_reference)
                        return MigrationResult(
                            source=source_ref,
                            destination=dest_ref,
                            success=True,
                            skipped=True,
                            duration_seconds=time.time() - start_time,
                        )
                except ManifestTransferError:
                    pass  # Chart doesn't exist, proceed with migration
            
            # In dry run mode, we don't need initialized clients
            if self.config.dry_run:
                logger.info(f"[DRY RUN] Would migrate chart: {source_ref} -> {dest_ref}")
                self.console.show_item_success(chart.full_reference)
                return MigrationResult(
                    source=source_ref,
                    destination=dest_ref,
                    success=True,
                    duration_seconds=time.time() - start_time,
                )

            # Copy the chart using native OCI transfer (same as images)
            # Charts are just OCI artifacts with specific media types, which RegistryClient handles natively.
            bytes_transferred = await self._source_client.copy_image(
                source_repo=source_repo,
                source_ref=chart.version,
                dest_client=self._dest_client,
                dest_repo=dest_repo,
                dest_ref=chart.version,
                layer_concurrency=self.config.layer_concurrency,
                blob_cache=self._blob_cache,
            )

            duration = time.time() - start_time
            size_mb = bytes_transferred / (1024 * 1024)
            self.console.show_item_success(chart.full_reference, size_mb)

            return MigrationResult(
                source=source_ref,
                destination=dest_ref,
                success=True,
                duration_seconds=duration,
                size_bytes=bytes_transferred,
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
        """Migrate a batch of items with Producer-Consumer pattern."""
        # Use a queue to manage items, preventing memory explosion from creating too many tasks
        queue: asyncio.Queue = asyncio.Queue()
        for item in items:
            queue.put_nowait(item)
            
        final_results: list[MigrationResult] = []
        last_checkpoint_time = time.time()
        checkpoint_lock = asyncio.Lock()
        
        async def worker():
            nonlocal last_checkpoint_time
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                
                try:
                    item_type, ref = item
                    ref_key = ref.full_reference if hasattr(ref, 'full_reference') else str(ref)
                    
                    # Skip if already processed (resume support)
                    if self._state and self._state.is_item_processed(item_type, ref_key):
                        progress_task.advance(1)
                        skipped_result = MigrationResult(
                            source=ref_key,
                            destination="",
                            success=True,
                            skipped=True,
                        )
                        final_results.append(skipped_result)
                        queue.task_done()
                        continue
                    
                    try:
                        result = await self.migrate_item(item)
                        self.add_result(result)
                        final_results.append(result)
                        progress_task.advance(1)
                        
                        # Update per-repository stats
                        if item_type == "image" and hasattr(ref, 'repository'):
                            repo = ref.repository
                            if repo in self._repo_stats:
                                if result.success:
                                    if result.skipped:
                                        self._repo_stats[repo]["skipped"] += 1
                                    else:
                                        self._repo_stats[repo]["migrated"] += 1
                                else:
                                    self._repo_stats[repo]["failed"] += 1
                        
                        # Update state
                        if self._state:
                            if result.success:
                                if result.skipped:
                                    self._state.mark_skipped(item_type, ref_key)
                                else:
                                    self._state.mark_completed(item_type, ref_key)
                            else:
                                self._state.mark_failed(item_type, ref_key, result.error or "Unknown error")
                            
                            # Checkpoint saving
                            self._checkpoint_counter += 1
                            async with checkpoint_lock:
                                should_checkpoint = (
                                    self._checkpoint_counter >= self.config.checkpoint_interval or
                                    time.time() - last_checkpoint_time > 30
                                )
                                if should_checkpoint:
                                    await self._state.save()
                                    self._checkpoint_counter = 0
                                    last_checkpoint_time = time.time()
                                    
                    except Exception as e:
                        # Handle migration exception
                        logger.exception(f"Error migrating {ref_key}")
                        error_result = MigrationResult(
                            source=ref_key,
                            destination="",
                            success=False,
                            error=str(e),
                        )
                        final_results.append(error_result)
                        if self._state:
                            self._state.mark_failed(item_type, ref_key, str(e))
                            
                except asyncio.CancelledError:
                    # Clean exit on cancellation
                    return
                except KeyboardInterrupt:
                    # Clean exit on interrupt
                    return
                except Exception as e:
                    logger.error(f"Worker critical error: {e}")
                finally:
                    queue.task_done()

        # Start workers
        workers = [
            asyncio.create_task(worker()) 
            for _ in range(self.config.parallel_jobs)
        ]
        
        try:
            # Wait for all workers to complete
            await asyncio.gather(*workers)
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.warning("Migration interrupted - stopping workers...")
            # Cancel all running workers
            for w in workers:
                w.cancel()
            # Wait for workers to finish (suppress errors)
            await asyncio.gather(*workers, return_exceptions=True)
            raise

        # Final checkpoint save
        if self._state:
            await self._state.save()

        return final_results

    async def run(self) -> MigrationSummary:
        """Execute the complete migration with resume capability."""
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
            # Initialize state persistence (for resume capability)
            if self.config.resume and not self.config.dry_run:
                state_dir = Path(self.config.state_dir)
                self._state = await MigrationState.load_or_create(
                    source_registry=self.config.source.registry_host,
                    dest_registry=self.config.destination.registry_host,
                    source_namespace=self.config.source.repository_prefix,
                    dest_namespace=self.config.destination.repository_prefix,
                    state_dir=state_dir,
                    resume=self.config.resume,
                )
                
                # Initialize blob cache using state
                self._blob_cache = BlobCache(self._state)
                
                # Check if resuming
                processed, total = self._state.progress
                if processed > 0 and total > 0:
                    self.console.show_info(
                        f"Resuming migration: {processed}/{total} items already processed"
                    )
            else:
                # No state persistence for dry run, but still use blob cache
                self._blob_cache = BlobCache()

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

            # Update state with total discovered
            if self._state:
                self._state.set_total_discovered(len(items))
                await self._state.save()

            # Confirm migration (unless dry run)
            if not self.config.dry_run:
                # Calculate unprocessed items for confirmation
                if self._state:
                    unprocessed = sum(
                        1 for item_type, ref in items
                        if not self._state.is_item_processed(
                            item_type,
                            ref.full_reference if hasattr(ref, 'full_reference') else str(ref)
                        )
                    )
                    processed = len(items) - unprocessed
                else:
                    unprocessed = len(items)
                    processed = 0
                
                if unprocessed > 10:
                    msg = f"Migrate {unprocessed} items?"
                    if processed > 0:
                        msg = f"Found {len(items)} items ({processed} already processed). Migrate remaining {unprocessed} items?"
                    else:
                        msg = f"Migrate {unprocessed} items?"
                    
                    if not self.console.confirm(f"{msg} This may take a while."):
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

            # Log per-repository accountability summary
            self._log_repository_summary()

            # Log blob cache stats
            if self._blob_cache:
                self._blob_cache.log_stats()

            if self.config.dry_run:
                self.console.show_dry_run_notice()

            # Clean up state file on successful completion
            if self._state and summary.failed == 0:
                await self._state.cleanup()
                logger.info("Migration completed successfully, state file cleaned up")

            self.log_complete(summary)
            return summary

        except Exception as e:
            logger.exception("Migration failed")
            self.console.show_error("Migration Failed", str(e))
            
            # Save state on failure for resume
            if self._state:
                await self._state.save()
                logger.info(f"State saved for resume. Run same command to continue.")
            
            return self.finalize_summary(start_time)

        finally:
            # Cleanup
            if self._source_client:
                await self._source_client.close()
            if self._dest_client:
                await self._dest_client.close()

    def _log_repository_summary(self) -> None:
        """Log per-repository migration accountability summary."""
        if not self._repo_stats:
            return
        
        # Calculate totals
        total_repos = len(self._repo_stats)
        total_discovered = sum(s.get("discovered", 0) for s in self._repo_stats.values())
        total_migrated = sum(s.get("migrated", 0) for s in self._repo_stats.values())
        total_failed = sum(s.get("failed", 0) for s in self._repo_stats.values())
        total_skipped = sum(s.get("skipped", 0) for s in self._repo_stats.values())
        
        # Log overall summary
        logger.info(
            f"Repository Summary: {total_repos} repos, "
            f"{total_discovered} tags discovered, "
            f"{total_migrated} migrated, "
            f"{total_failed} failed, "
            f"{total_skipped} skipped"
        )
        
        # Find repos with issues (failed or incomplete)
        repos_with_issues = []
        for repo, stats in self._repo_stats.items():
            discovered = stats.get("discovered", 0)
            migrated = stats.get("migrated", 0)
            failed = stats.get("failed", 0)
            skipped = stats.get("skipped", 0)
            scan_error = stats.get("scan_error")
            
            if scan_error:
                repos_with_issues.append((repo, f"scan failed: {scan_error}"))
            elif failed > 0:
                repos_with_issues.append((repo, f"{failed}/{discovered} tags failed"))
            elif migrated + skipped < discovered:
                unprocessed = discovered - migrated - skipped
                repos_with_issues.append((repo, f"{unprocessed}/{discovered} tags not processed"))
        
        # Log repos with issues
        if repos_with_issues:
            logger.warning(f"Repositories with issues ({len(repos_with_issues)}):")
            for repo, issue in repos_with_issues[:20]:  # Limit to 20 for log readability
                logger.warning(f"  - {repo}: {issue}")
            if len(repos_with_issues) > 20:
                logger.warning(f"  ... and {len(repos_with_issues) - 20} more")
        else:
            logger.info("All repositories migrated successfully with full accountability")
