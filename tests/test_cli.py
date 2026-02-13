"""
Unit tests for the CLI module (main.py).

Tests the Typer CLI commands, version callback, and info command.
"""
import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock

from main import app
from config import MigrationSummary
from utilities.version import version

runner = CliRunner()


class TestCLIHelp:
    """Tests for CLI help and version commands."""
    
    def test_main_help(self):
        """Test main help command."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        # Check for key elements in help output
        assert "migrate" in result.output.lower() or "🚀" in result.output
    
    def test_version(self):
        """Test version flag."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert version in result.output
    
    def test_migrate_help(self):
        """Test migrate command help."""
        result = runner.invoke(app, ["migrate", "--help"])
        assert result.exit_code == 0
        assert "container-registry" in result.output
    
    def test_migrate_container_registry_help(self):
        """Test migrate container-registry help."""
        result = runner.invoke(app, ["migrate", "container-registry", "--help"])
        assert result.exit_code == 0
        assert "--source-registry" in result.output
        assert "--destination-registry" in result.output


class TestInfoCommand:
    """Tests for the info command."""
    
    def test_info_command(self):
        """Test info command execution."""
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 0
        assert "Python" in result.output
        assert "Platform" in result.output


class TestMigrateContainerRegistry:
    """Tests for migrate container-registry command."""
    
    def test_missing_required_args(self):
        """Test missing required arguments."""
        result = runner.invoke(app, ["migrate", "container-registry"])
        assert result.exit_code != 0
    
    def test_dry_run_success(self):
        """Test successful dry run migration."""
        mock_summary = MigrationSummary(
            total_items=10,
            successful=10,
            failed=0,
            total_duration_seconds=5.0,
        )
        
        with patch('asyncio.run', return_value=mock_summary):
            result = runner.invoke(app, [
                "migrate", "container-registry",
                "--source-registry", "src.io",
                "--source-user", "user",
                "--source-password", "pass",
                "--destination-registry", "dst.io",
                "--destination-user", "user",
                "--destination-password", "pass",
                "--dry-run",
            ])
            assert result.exit_code == 0
    
    def test_with_filters(self):
        """Test migration with include/exclude filters."""
        mock_summary = MigrationSummary(
            total_items=5,
            successful=5,
            failed=0,
        )
        
        with patch('asyncio.run', return_value=mock_summary):
            result = runner.invoke(app, [
                "migrate", "container-registry",
                "--source-registry", "src.io",
                "--source-user", "user",
                "--source-password", "pass",
                "--destination-registry", "dst.io",
                "--destination-user", "user",
                "--destination-password", "pass",
                "--include", "^prod-",
                "--exclude", ".*-test$",
                "--dry-run",
            ])
            assert result.exit_code == 0
    
    def test_with_parallel_option(self):
        """Test migration with parallel jobs option."""
        mock_summary = MigrationSummary(
            total_items=5,
            successful=5,
            failed=0,
        )
        
        with patch('asyncio.run', return_value=mock_summary):
            result = runner.invoke(app, [
                "migrate", "container-registry",
                "--source-registry", "src.io",
                "--source-user", "user",
                "--source-password", "pass",
                "--destination-registry", "dst.io",
                "--destination-user", "user",
                "--destination-password", "pass",
                "--parallel", "8",
                "--dry-run",
            ])
            assert result.exit_code == 0
    
    def test_with_failures_exit_code(self):
        """Test exit code when there are failures."""
        mock_summary = MigrationSummary(
            total_items=5,
            successful=3,
            failed=2,
        )
        
        with patch('asyncio.run', return_value=mock_summary):
            result = runner.invoke(app, [
                "migrate", "container-registry",
                "--source-registry", "src.io",
                "--source-user", "user",
                "--source-password", "pass",
                "--destination-registry", "dst.io",
                "--destination-user", "user",
                "--destination-password", "pass",
                "--dry-run",
            ])
            # Exit code 1 when there are failures
            assert result.exit_code == 1
    
    def test_with_skip_existing(self):
        """Test migration with skip-existing flag."""
        mock_summary = MigrationSummary(
            total_items=5,
            successful=3,
            failed=0,
            skipped=2,
        )
        
        with patch('asyncio.run', return_value=mock_summary):
            result = runner.invoke(app, [
                "migrate", "container-registry",
                "--source-registry", "src.io",
                "--source-user", "user",
                "--source-password", "pass",
                "--destination-registry", "dst.io",
                "--destination-user", "user",
                "--destination-password", "pass",
                "--skip-existing",
                "--dry-run",
            ])
            assert result.exit_code == 0

    def test_non_interactive_flag(self):
        """Test migration with non-interactive flag."""
        mock_summary = MigrationSummary(
            total_items=5,
            successful=5,
            failed=0,
        )
        
        with patch('asyncio.run', return_value=mock_summary):
            result = runner.invoke(app, [
                "migrate", "container-registry",
                "--source-registry", "src.io",
                "--source-user", "user",
                "--source-password", "pass",
                "--destination-registry", "dst.io",
                "--destination-user", "user",
                "--destination-password", "pass",
                "--non-interactive",
                "--dry-run",
            ])
            assert result.exit_code == 0
