"""
Unit tests for the CLI module (main.py).

Tests the Typer CLI commands, version callback, and info command.
"""
import pytest
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock

from main import app, check_tools
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
    
    def test_info_command_all_tools(self):
        """Test info command when all tools are available."""
        with patch('main.check_tools', return_value={"docker": True, "helm": True}):
            result = runner.invoke(app, ["info"])
            assert result.exit_code == 0
    
    def test_info_command_no_tools(self):
        """Test info command when no tools are available."""
        with patch('shutil.which', return_value=None):
            result = runner.invoke(app, ["info"])
            assert result.exit_code == 0


class TestCheckTools:
    """Tests for check_tools function."""
    
    def test_all_tools_available(self):
        """Test when all tools are available."""
        with patch('shutil.which') as mock_which:
            mock_which.side_effect = lambda x: f"/usr/bin/{x}"
            tools = check_tools()
            assert tools["docker"] is True
            assert tools["helm"] is True
    
    def test_no_tools_available(self):
        """Test when no tools are available."""
        with patch('shutil.which', return_value=None):
            tools = check_tools()
            assert tools["docker"] is False
            assert tools["helm"] is False
    
    def test_partial_tools(self):
        """Test when some tools are available."""
        def mock_which(cmd):
            return "/usr/bin/docker" if cmd == "docker" else None
        
        with patch('shutil.which', side_effect=mock_which):
            tools = check_tools()
            assert tools["docker"] is True
            assert tools["helm"] is False


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
