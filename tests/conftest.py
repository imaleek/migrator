"""
Test configuration and fixtures for Migrator tests.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from typing import Generator
import sys
from pathlib import Path

# Add src to path for imports
src_path = Path(__file__).parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Configure pytest-asyncio
pytest_plugins = ('pytest_asyncio',)


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_httpx_client() -> MagicMock:
    """Create a mock httpx AsyncClient."""
    client = AsyncMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.put = AsyncMock()
    client.head = AsyncMock()
    client.patch = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def sample_registry_credentials():
    """Sample registry credentials for testing."""
    from config import RegistryCredentials
    return RegistryCredentials(
        registry="test.registry.io",
        username="testuser",
        password="testpass",
        insecure=False,
    )


@pytest.fixture
def sample_migration_config():
    """Sample migration configuration for testing."""
    from config import MigrationConfig, RegistryCredentials
    
    source = RegistryCredentials(
        registry="source.registry.io",
        username="sourceuser",
        password="sourcepass",
    )
    destination = RegistryCredentials(
        registry="dest.registry.io",
        username="destuser",
        password="destpass",
    )
    
    return MigrationConfig(
        source=source,
        destination=destination,
        parallel_jobs=4,
        dry_run=False,
        skip_existing=True,
    )


@pytest.fixture
def mock_console():
    """Mock MigrationConsole for testing."""
    console = MagicMock()
    console.show_info = MagicMock()
    console.show_error = MagicMock()
    console.show_warning = MagicMock()
    console.show_item_success = MagicMock()
    console.show_item_failure = MagicMock()
    console.show_item_skipped = MagicMock()
    console.start_discovery = MagicMock()
    console.show_discovery_results = MagicMock()
    console.print_banner = MagicMock()
    console.print_config_summary = MagicMock()
    console.show_summary = MagicMock()
    console.show_dry_run_notice = MagicMock()
    console.confirm = MagicMock(return_value=True)
    console.status = MagicMock()
    console.migration_progress = MagicMock()
    return console
