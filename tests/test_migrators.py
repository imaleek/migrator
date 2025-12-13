"""
Unit tests for the Migrators module.

Tests the BaseMigrator abstract class and ContainerRegistryMigrator
including connection validation, discovery, filtering, and migration.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import time

from migrators import BaseMigrator, MigrationContext
from migrators.container_registry import ContainerRegistryMigrator
from config import (
    MigrationConfig,
    RegistryCredentials,
    MigrationSummary,
    MigrationResult,
    ImageReference,
    ChartReference,
)


class TestMigrationContext:
    """Tests for MigrationContext dataclass."""
    
    def test_creation(self, sample_migration_config, mock_console):
        """Test context creation."""
        context = MigrationContext(
            config=sample_migration_config,
            console=mock_console,
        )
        assert context.config == sample_migration_config
        assert context.console == mock_console
    
    def test_elapsed_seconds(self, sample_migration_config, mock_console):
        """Test elapsed time calculation."""
        start = time.time()
        context = MigrationContext(
            config=sample_migration_config,
            console=mock_console,
            start_time=start,
        )
        time.sleep(0.05)
        elapsed = context.elapsed_seconds
        assert elapsed >= 0.05


class TestContainerRegistryMigratorInit:
    """Tests for ContainerRegistryMigrator initialization."""
    
    @pytest.fixture
    def migrator(self, sample_migration_config, mock_console):
        return ContainerRegistryMigrator(sample_migration_config, mock_console)
    
    def test_init(self, migrator):
        """Test migrator initialization."""
        assert migrator._source_client is None
        assert migrator._dest_client is None
    
    def test_name_property(self, migrator):
        """Test name property."""
        assert migrator.name == "Container Registry"
    
    def test_description_property(self, migrator):
        """Test description property."""
        assert "container images" in migrator.description.lower()


class TestContainerRegistryMigratorResults:
    """Tests for result tracking in ContainerRegistryMigrator."""
    
    @pytest.fixture
    def migrator(self, sample_migration_config, mock_console):
        return ContainerRegistryMigrator(sample_migration_config, mock_console)
    
    def test_add_result_success(self, migrator):
        """Test adding successful result."""
        result = MigrationResult(
            source="source.io/app:v1",
            destination="dest.io/app:v1",
            success=True,
            size_bytes=1024,
        )
        migrator.add_result(result)
        
        assert migrator._summary.total_items == 1
        assert migrator._summary.successful == 1
        assert migrator._summary.failed == 0
    
    def test_add_result_failed(self, migrator):
        """Test adding failed result."""
        result = MigrationResult(
            source="source.io/app:v1",
            destination="dest.io/app:v1",
            success=False,
            error="Connection error",
        )
        migrator.add_result(result)
        
        assert migrator._summary.total_items == 1
        assert migrator._summary.failed == 1
    
    def test_add_result_skipped(self, migrator):
        """Test adding skipped result."""
        result = MigrationResult(
            source="source.io/app:v1",
            destination="dest.io/app:v1",
            success=True,
            skipped=True,
        )
        migrator.add_result(result)
        
        assert migrator._summary.skipped == 1
    
    def test_finalize_summary(self, migrator):
        """Test finalizing summary with duration."""
        start_time = time.time() - 10
        result = MigrationResult(
            source="source.io/app:v1",
            destination="dest.io/app:v1",
            success=True,
        )
        migrator.add_result(result)
        
        summary = migrator.finalize_summary(start_time)
        assert summary.total_duration_seconds >= 10


class TestContainerRegistryMigratorFilters:
    """Tests for repository filtering in ContainerRegistryMigrator."""
    
    @pytest.fixture
    def migrator(self, sample_migration_config, mock_console):
        return ContainerRegistryMigrator(sample_migration_config, mock_console)
    
    def test_matches_filter_no_patterns(self, migrator):
        """Test filter matching with no patterns."""
        assert migrator._matches_filter("any-repo") is True
    
    def test_matches_filter_include_pattern(self, sample_migration_config, mock_console):
        """Test filter matching with include pattern."""
        sample_migration_config.include_pattern = "^prod-"
        migrator = ContainerRegistryMigrator(sample_migration_config, mock_console)
        
        assert migrator._matches_filter("prod-app") is True
        assert migrator._matches_filter("dev-app") is False
    
    def test_matches_filter_exclude_pattern(self, sample_migration_config, mock_console):
        """Test filter matching with exclude pattern."""
        sample_migration_config.exclude_pattern = ".*-test$"
        migrator = ContainerRegistryMigrator(sample_migration_config, mock_console)
        
        assert migrator._matches_filter("app-test") is False
        assert migrator._matches_filter("app-prod") is True
    
    def test_matches_filter_both_patterns(self, sample_migration_config, mock_console):
        """Test filter matching with both patterns."""
        sample_migration_config.include_pattern = "^prod-"
        sample_migration_config.exclude_pattern = ".*-deprecated$"
        migrator = ContainerRegistryMigrator(sample_migration_config, mock_console)
        
        assert migrator._matches_filter("prod-app") is True
        assert migrator._matches_filter("prod-deprecated") is False
        assert migrator._matches_filter("dev-app") is False


class TestContainerRegistryMigratorConnection:
    """Tests for connection validation in ContainerRegistryMigrator."""
    
    @pytest.fixture
    def config(self):
        source = RegistryCredentials(
            registry="source.io", username="u", password="p"
        )
        dest = RegistryCredentials(
            registry="dest.io", username="u", password="p"
        )
        return MigrationConfig(source=source, destination=dest)
    
    @pytest.mark.asyncio
    async def test_validate_connection_source_fails(self, config, mock_console):
        """Test validation when source connection fails."""
        from services.registry_client import RegistryClient, RegistryConnectionError
        
        migrator = ContainerRegistryMigrator(config, mock_console)
        
        with patch.object(RegistryClient, 'connect', new_callable=AsyncMock), \
             patch.object(RegistryClient, 'check_connectivity',
                         new_callable=AsyncMock,
                         side_effect=RegistryConnectionError("source.io", "refused")):
            
            result = await migrator.validate_connection()
            assert result is False
            mock_console.show_error.assert_called()


class TestContainerRegistryMigratorDiscovery:
    """Tests for item discovery in ContainerRegistryMigrator."""
    
    @pytest.fixture
    def migrator(self, sample_migration_config, mock_console):
        return ContainerRegistryMigrator(sample_migration_config, mock_console)
    
    @pytest.mark.asyncio
    async def test_discover_empty(self, migrator, mock_console):
        """Test discovery with no repositories."""
        mock_source = AsyncMock()
        mock_source.list_repositories = AsyncMock(return_value=[])
        
        migrator._source_client = mock_source
        mock_console.status = MagicMock()
        mock_console.status.return_value.__enter__ = MagicMock()
        mock_console.status.return_value.__exit__ = MagicMock()
        
        items = await migrator.discover()
        assert len(items) == 0


class TestContainerRegistryMigratorMigration:
    """Tests for item migration in ContainerRegistryMigrator."""
    
    @pytest.fixture
    def config(self):
        source = RegistryCredentials(
            registry="source.io", username="u", password="p"
        )
        dest = RegistryCredentials(
            registry="dest.io", username="u", password="p"
        )
        return MigrationConfig(source=source, destination=dest)
    
    @pytest.mark.asyncio
    async def test_migrate_image_dry_run(self, sample_migration_config, mock_console):
        """Test image migration in dry run mode."""
        sample_migration_config.dry_run = True
        sample_migration_config.skip_existing = False
        migrator = ContainerRegistryMigrator(sample_migration_config, mock_console)
        
        image = ImageReference(repository="myapp", tag="v1.0")
        result = await migrator._migrate_image(image)
        
        assert result.success is True
        mock_console.show_item_success.assert_called()
    
    @pytest.mark.asyncio
    async def test_migrate_chart_dry_run(self, sample_migration_config, mock_console):
        """Test chart migration in dry run mode."""
        sample_migration_config.dry_run = True
        migrator = ContainerRegistryMigrator(sample_migration_config, mock_console)
        
        chart = ChartReference(name="mychart", version="1.0.0")
        result = await migrator._migrate_chart(chart)
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_migrate_item_unknown_type(self, sample_migration_config, mock_console):
        """Test migrate_item with unknown type."""
        migrator = ContainerRegistryMigrator(sample_migration_config, mock_console)
        result = await migrator.migrate_item(("unknown", "ref"))
        
        assert result.success is False
        assert "Unknown item type" in result.error
    
    @pytest.mark.asyncio
    async def test_migrate_image_skip_existing(self, config, mock_console):
        """Test image migration skips existing."""
        from services.registry_client import ManifestInfo
        
        config.skip_existing = True
        config.dry_run = False
        migrator = ContainerRegistryMigrator(config, mock_console)
        
        mock_source = AsyncMock()
        mock_dest = AsyncMock()
        
        mock_manifest = ManifestInfo(
            digest="sha256:abc",
            media_type="application/vnd.docker.distribution.manifest.v2+json",
            size=1024
        )
        mock_dest.get_manifest = AsyncMock(return_value=(mock_manifest, b"{}"))
        
        migrator._source_client = mock_source
        migrator._dest_client = mock_dest
        
        image = ImageReference(repository="myapp", tag="v1.0")
        result = await migrator._migrate_image(image)
        
        assert result.success is True
        assert result.skipped is True
    
    @pytest.mark.asyncio
    async def test_migrate_image_success(self, config, mock_console):
        """Test successful image migration."""
        config.skip_existing = False
        config.dry_run = False
        migrator = ContainerRegistryMigrator(config, mock_console)
        
        mock_source = AsyncMock()
        mock_dest = AsyncMock()
        
        mock_source.copy_image = AsyncMock(return_value=1024)
        
        migrator._source_client = mock_source
        migrator._dest_client = mock_dest
        
        image = ImageReference(repository="app", tag="v1.0")
        result = await migrator._migrate_image(image)
        
        assert result.success is True
        assert result.size_bytes == 1024
    
    @pytest.mark.asyncio
    async def test_migrate_image_transfer_error(self, config, mock_console):
        """Test image migration handles transfer error."""
        from services.registry_client import ImageTransferError
        
        config.skip_existing = False
        config.dry_run = False
        migrator = ContainerRegistryMigrator(config, mock_console)
        
        mock_source = AsyncMock()
        mock_dest = AsyncMock()
        
        mock_source.copy_image = AsyncMock(
            side_effect=ImageTransferError("app:v1.0", "connection lost")
        )
        
        migrator._source_client = mock_source
        migrator._dest_client = mock_dest
        
        image = ImageReference(repository="myapp", tag="v1.0")
        result = await migrator._migrate_image(image)
        
        assert result.success is False
        assert "connection lost" in result.error


class TestContainerRegistryMigratorLogging:
    """Tests for logging in ContainerRegistryMigrator."""
    
    @pytest.fixture
    def migrator(self, sample_migration_config, mock_console):
        return ContainerRegistryMigrator(sample_migration_config, mock_console)
    
    def test_log_start(self, migrator):
        """Test migration start logging."""
        import logging
        logging.getLogger("migrators").setLevel(logging.DEBUG)
        migrator.log_start()
    
    def test_log_complete(self, migrator):
        """Test migration complete logging."""
        import logging
        logging.getLogger("migrators").setLevel(logging.DEBUG)
        summary = MigrationSummary(
            total_items=10,
            successful=8,
            failed=2,
        )
        migrator.log_complete(summary)


class TestContainerRegistryMigratorRun:
    """Tests for full run flow."""
    
    @pytest.fixture
    def config(self):
        source = RegistryCredentials(
            registry="source.io", username="u", password="p"
        )
        dest = RegistryCredentials(
            registry="dest.io", username="u", password="p"
        )
        return MigrationConfig(source=source, destination=dest, dry_run=True)
    
    @pytest.mark.asyncio
    async def test_run_dry_run(self, config, mock_console):
        """Test full run in dry run mode."""
        from services.registry_client import RegistryClient
        
        migrator = ContainerRegistryMigrator(config, mock_console)
        
        # Setup mocks
        mock_console.status = MagicMock()
        mock_console.status.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_console.status.return_value.__exit__ = MagicMock(return_value=None)
        
        mock_progress = MagicMock()
        mock_progress.add_task = MagicMock(return_value=MagicMock())
        mock_progress.update = MagicMock()
        mock_console.migration_progress = MagicMock()
        mock_console.migration_progress.return_value.__enter__ = MagicMock(return_value=mock_progress)
        mock_console.migration_progress.return_value.__exit__ = MagicMock(return_value=None)
        
        with patch.object(RegistryClient, 'connect', new_callable=AsyncMock), \
             patch.object(RegistryClient, 'close', new_callable=AsyncMock), \
             patch.object(RegistryClient, 'check_connectivity', new_callable=AsyncMock, return_value=True), \
             patch.object(RegistryClient, 'list_repositories', new_callable=AsyncMock, return_value=["app"]), \
             patch.object(RegistryClient, 'list_tags', new_callable=AsyncMock, return_value=["v1.0"]):
            
            summary = await migrator.run()
            assert summary.total_items >= 0
