"""
Unit tests for the Helm Client (services/helm_client.py).

Tests the Helm CLI wrapper for OCI registry chart operations.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import tempfile

from services.helm_client import HelmClient, HelmNotFoundError, HelmChartError, ChartInfo
from config import RegistryCredentials, ChartReference


class TestHelmClientInit:
    """Tests for HelmClient initialization."""
    
    @pytest.fixture
    def credentials(self):
        return RegistryCredentials(
            registry="charts.example.com",
            username="user",
            password="pass",
        )
    
    @pytest.fixture
    def client(self, credentials):
        return HelmClient(credentials)
    
    def test_init(self, client, credentials):
        """Test client initialization."""
        assert client.credentials == credentials
        assert client._helm_path is None
        assert client._logged_in is False
    
    def test_registry_property(self, client):
        """Test registry property."""
        assert client.registry == "charts.example.com"
    
    def test_oci_url_property(self, client):
        """Test OCI URL property."""
        assert client.oci_url == "oci://charts.example.com"


class TestHelmClientInitialize:
    """Tests for HelmClient initialization flow."""
    
    @pytest.fixture
    def client(self):
        creds = RegistryCredentials(
            registry="charts.io", username="u", password="p"
        )
        return HelmClient(creds)
    
    @pytest.mark.asyncio
    async def test_initialize_no_helm(self, client):
        """Test initialization when Helm is not found."""
        with patch('shutil.which', return_value=None):
            with pytest.raises(HelmNotFoundError):
                await client.initialize()
    
    @pytest.mark.asyncio
    async def test_initialize_success(self, client):
        """Test successful initialization."""
        with patch('shutil.which', return_value='/usr/bin/helm'):
            async def mock_run_helm(args, **kwargs):
                if "version" in args:
                    return "v3.12.0"
                return ""
            
            client._run_helm = mock_run_helm
            await client.initialize()
            assert client._helm_path == '/usr/bin/helm'


class TestHelmClientAuth:
    """Tests for HelmClient authentication."""
    
    @pytest.fixture
    def client(self):
        creds = RegistryCredentials(
            registry="charts.io", username="u", password="p"
        )
        return HelmClient(creds)
    
    @pytest.mark.asyncio
    async def test_login_success(self, client):
        """Test successful login."""
        client._helm_path = '/usr/bin/helm'
        
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"Login succeeded", b""))
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            result = await client.login()
            assert result is True
            assert client._logged_in is True
    
    @pytest.mark.asyncio
    async def test_login_failure(self, client):
        """Test failed login."""
        client._helm_path = '/usr/bin/helm'
        
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"Invalid credentials"))
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            result = await client.login()
            assert result is False
            assert client._logged_in is False
    
    @pytest.mark.asyncio
    async def test_login_already_logged_in(self, client):
        """Test login when already logged in."""
        client._logged_in = True
        result = await client.login()
        assert result is True
    
    @pytest.mark.asyncio
    async def test_login_exception(self, client):
        """Test login with exception."""
        client._helm_path = '/usr/bin/helm'
        
        with patch('asyncio.create_subprocess_exec', side_effect=Exception("error")):
            result = await client.login()
            assert result is False


class TestHelmClientCommands:
    """Tests for HelmClient command execution."""
    
    @pytest.fixture
    def client(self):
        creds = RegistryCredentials(
            registry="charts.io", username="u", password="p"
        )
        return HelmClient(creds)
    
    @pytest.mark.asyncio
    async def test_run_helm_success(self, client):
        """Test running helm command successfully."""
        client._helm_path = '/usr/bin/helm'
        
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"output", b""))
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            result = await client._run_helm(["version", "--short"])
            assert result == "output"
    
    @pytest.mark.asyncio
    async def test_list_charts(self, client):
        """Test list charts (returns empty by design)."""
        charts = await client.list_charts()
        assert charts == []


class TestHelmClientCleanup:
    """Tests for HelmClient cleanup."""
    
    @pytest.fixture
    def client(self):
        creds = RegistryCredentials(
            registry="charts.io", username="u", password="p"
        )
        return HelmClient(creds)
    
    @pytest.mark.asyncio
    async def test_cleanup_with_temp_dir(self, client):
        """Test cleanup with temp directory."""
        client._temp_dir = Path(tempfile.mkdtemp())
        client._logged_in = False
        client._helm_path = '/usr/bin/helm'
        
        await client.cleanup()
        
        assert client._temp_dir is None or not client._temp_dir.exists()
    
    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager."""
        creds = RegistryCredentials(
            registry="charts.io", username="u", password="p"
        )
        client = HelmClient(creds)
        
        with patch('shutil.which', return_value='/usr/bin/helm'):
            async def mock_run(*args, **kwargs):
                return "v3.12.0"
            client._run_helm = mock_run
            
            async with client as c:
                assert c._helm_path == '/usr/bin/helm'


class TestChartInfo:
    """Tests for ChartInfo dataclass."""
    
    def test_creation(self):
        """Test ChartInfo creation."""
        info = ChartInfo(name="mychart", version="1.0.0", description="My chart")
        assert info.name == "mychart"
        assert info.version == "1.0.0"
    
    def test_full_name(self):
        """Test full_name property."""
        info = ChartInfo(name="mychart", version="1.0.0")
        assert info.full_name == "mychart:1.0.0"
    
    def test_default_values(self):
        """Test default values."""
        info = ChartInfo(name="mychart", version="1.0.0")
        assert info.description == ""
        assert info.app_version == ""
        assert info.digest == ""
        assert info.created == ""


class TestHelmExceptions:
    """Tests for Helm client exceptions."""
    
    def test_helm_not_found_error(self):
        """Test HelmNotFoundError."""
        error = HelmNotFoundError()
        assert "Helm CLI not found" in str(error)
        assert "3.8" in str(error)
    
    def test_helm_chart_error(self):
        """Test HelmChartError."""
        error = HelmChartError("mychart:1.0.0", "push", "Permission denied")
        assert "mychart:1.0.0" in str(error)
        assert "push" in str(error)
        assert "Permission denied" in str(error)
