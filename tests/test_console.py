"""
Unit tests for the Console UI module.

Tests the MigrationConsole class for Rich-based terminal output
including banners, summaries, progress bars, and status updates.
"""
import pytest
from unittest.mock import MagicMock, patch

from console import MigrationConsole, ItemStatus, MigrationItem
from config import MigrationSummary, MigrationResult


class TestMigrationConsoleInit:
    """Tests for MigrationConsole initialization."""
    
    def test_init_default(self):
        """Test default console initialization."""
        console = MigrationConsole()
        assert console.verbose is False
    
    def test_init_verbose(self):
        """Test verbose console initialization."""
        console = MigrationConsole(verbose=True)
        assert console.verbose is True
    
    def test_colors_defined(self):
        """Test that color scheme is defined."""
        console = MigrationConsole()
        assert "primary" in console.COLORS
        assert "success" in console.COLORS
        assert "error" in console.COLORS
        assert "warning" in console.COLORS


class TestMigrationConsoleBanner:
    """Tests for banner and summary printing."""
    
    @pytest.fixture
    def console(self):
        return MigrationConsole(verbose=False)
    
    def test_print_banner(self, console):
        """Test banner printing."""
        console.print_banner()
    
    def test_print_config_summary(self, console):
        """Test config summary printing."""
        console.print_config_summary(
            source_registry="source.io",
            dest_registry="dest.io",
            parallel_jobs=4,
            dry_run=False,
        )
    
    def test_print_config_summary_dry_run(self, console):
        """Test config summary with dry run."""
        console.print_config_summary(
            source_registry="source.io",
            dest_registry="dest.io",
            parallel_jobs=4,
            dry_run=True,
        )


class TestMigrationConsoleToolStatus:
    """Tests for tool status display."""
    
    @pytest.fixture
    def console(self):
        return MigrationConsole()
    
    def test_print_tool_status_all_available(self, console):
        """Test tool status when all tools available."""
        tools = {"docker": True, "helm": True}
        console.print_tool_status(tools)
    
    def test_print_tool_status_none_available(self, console):
        """Test tool status when no tools available."""
        tools = {"docker": False, "helm": False}
        console.print_tool_status(tools)
    
    def test_print_tool_status_partial(self, console):
        """Test tool status with partial availability."""
        tools = {"docker": True, "helm": False}
        console.print_tool_status(tools)


class TestMigrationConsoleItemStatus:
    """Tests for item status display."""
    
    @pytest.fixture
    def console(self):
        return MigrationConsole(verbose=True)
    
    def test_show_item_success_verbose(self, console):
        """Test showing successful item in verbose mode."""
        console.show_item_success("app:v1.0", size_mb=5.5)
    
    def test_show_item_failure(self, console):
        """Test showing failed item."""
        console.show_item_failure("app:v1.0", "Connection error")
    
    def test_show_item_skipped(self):
        """Test showing skipped item."""
        console = MigrationConsole(verbose=True)
        console.show_item_skipped("app:v1.0", "already exists")


class TestMigrationConsoleMessages:
    """Tests for message display methods."""
    
    @pytest.fixture
    def console(self):
        return MigrationConsole()
    
    def test_show_error(self, console):
        """Test showing error."""
        console.show_error("Test Error", "Something went wrong")
    
    def test_show_warning(self, console):
        """Test showing warning."""
        console.show_warning("This is a warning")
    
    def test_show_info(self, console):
        """Test showing info."""
        console.show_info("This is info")
    
    def test_show_dry_run_notice(self, console):
        """Test showing dry run notice."""
        console.show_dry_run_notice()


class TestMigrationConsoleProgress:
    """Tests for progress display."""
    
    @pytest.fixture
    def console(self):
        return MigrationConsole()
    
    def test_create_progress(self, console):
        """Test progress bar creation."""
        progress = console.create_progress()
        assert progress is not None
    
    def test_create_progress_and_task(self):
        """Test creating progress and adding task."""
        console = MigrationConsole(verbose=True)
        progress = console.create_progress()
        
        with progress:
            task = progress.add_task("Test", total=10)
            progress.update(task, advance=5)
    
    def test_migration_progress_context(self, console):
        """Test migration progress context manager."""
        with console.migration_progress(10) as progress:
            task = progress.add_task("Migrating", total=10)
            progress.update(task, advance=1)
    
    def test_status_context(self, console):
        """Test status context manager."""
        with console.status("Processing..."):
            pass


class TestMigrationConsoleSummary:
    """Tests for summary display."""
    
    @pytest.fixture
    def console(self):
        return MigrationConsole()
    
    def test_show_summary_all_success(self, console):
        """Test showing successful summary."""
        summary = MigrationSummary(
            total_items=10,
            successful=10,
            failed=0,
            total_duration_seconds=30.5,
            total_size_bytes=1024 * 1024 * 100,
        )
        console.show_summary(summary)
    
    def test_show_summary_with_failures(self, console):
        """Test showing summary with failures."""
        summary = MigrationSummary(
            total_items=10,
            successful=7,
            failed=3,
            results=[
                MigrationResult(
                    source="source.io/app:v1",
                    destination="dest.io/app:v1",
                    success=False,
                    error="Timeout",
                ),
            ],
        )
        console.show_summary(summary)
    
    def test_show_summary_all_skipped(self, console):
        """Test summary when all items skipped."""
        summary = MigrationSummary(
            total_items=5,
            successful=0,
            failed=0,
            skipped=5,
        )
        console.show_summary(summary)
    
    def test_show_summary_all_failed(self, console):
        """Test summary when all items failed."""
        summary = MigrationSummary(
            total_items=5,
            successful=0,
            failed=5,
            skipped=0,
        )
        console.show_summary(summary)


class TestItemStatus:
    """Tests for ItemStatus enum."""
    
    def test_status_values(self):
        """Test all status values are defined."""
        assert ItemStatus.PENDING == "pending"
        assert ItemStatus.SUCCESS == "success"
        assert ItemStatus.FAILED == "failed"
        assert ItemStatus.SKIPPED == "skipped"


class TestMigrationItem:
    """Tests for MigrationItem dataclass."""
    
    def test_creation(self):
        """Test MigrationItem creation."""
        item = MigrationItem(
            name="app:v1.0",
            source="source.io/app:v1.0",
            destination="dest.io/app:v1.0",
        )
        assert item.name == "app:v1.0"
        assert item.status == ItemStatus.PENDING
    
    def test_with_error(self):
        """Test MigrationItem with error."""
        item = MigrationItem(
            name="app:v1.0",
            source="source.io/app:v1.0",
            destination="dest.io/app:v1.0",
            status=ItemStatus.FAILED,
            error="Connection refused",
        )
        assert item.error == "Connection refused"
    
    def test_with_size(self):
        """Test MigrationItem with size."""
        item = MigrationItem(
            name="app:v1.0",
            source="source.io/app:v1.0",
            destination="dest.io/app:v1.0",
            status=ItemStatus.SUCCESS,
            size_bytes=1024,
        )
        assert item.size_bytes == 1024
