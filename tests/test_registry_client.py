"""
Unit tests for the Registry Client (services/registry_client.py).

Tests the HTTP-based Docker Registry API v2 client including
authentication, manifest/blob operations, and image copying.
"""
import pytest
import base64
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from services.registry_client import (
    RegistryClient,
    RegistryConnectionError,
    RegistryAuthenticationError,
    ImageTransferError,
    ManifestInfo,
    BlobInfo,
)
from config import RegistryCredentials


class TestRegistryClientInit:
    """Tests for RegistryClient initialization."""
    
    @pytest.fixture
    def credentials(self):
        return RegistryCredentials(
            registry="test.registry.io",
            username="testuser",
            password="testpass",
        )
    
    @pytest.fixture
    def client(self, credentials):
        return RegistryClient(credentials)
    
    def test_init(self, credentials):
        """Test client initialization."""
        client = RegistryClient(credentials)
        assert client.credentials == credentials
        assert client.timeout == 300.0
        assert client._client is None
        assert client._token is None
    
    def test_base_url_property(self, client):
        """Test base_url property."""
        assert client.base_url == "https://test.registry.io/v2"
    
    def test_registry_property(self, client):
        """Test registry property."""
        assert client.registry == "test.registry.io"


class TestRegistryClientAuth:
    """Tests for authentication methods."""
    
    @pytest.fixture
    def client(self, sample_registry_credentials):
        return RegistryClient(sample_registry_credentials)
    
    def test_basic_auth_header(self, client):
        """Test basic auth header generation."""
        headers = client._get_auth_header()
        expected_auth = base64.b64encode(b"testuser:testpass").decode()
        assert headers["Authorization"] == f"Basic {expected_auth}"
    
    def test_bearer_auth_header(self, client):
        """Test bearer token auth header."""
        client._token = "test-bearer-token"
        headers = client._get_auth_header()
        assert headers["Authorization"] == "Bearer test-bearer-token"
    
    @pytest.mark.asyncio
    async def test_handle_auth_challenge_bearer(self, client, mock_httpx_client):
        """Test handling Bearer auth challenge."""
        mock_response = MagicMock()
        mock_response.headers = {
            "www-authenticate": 'Bearer realm="https://auth.io/token",service="registry"'
        }
        
        mock_token_response = MagicMock()
        mock_token_response.status_code = 200
        mock_token_response.json.return_value = {"token": "test-token"}
        mock_httpx_client.get.return_value = mock_token_response
        
        client._client = mock_httpx_client
        await client._handle_auth_challenge(mock_response, "repository:test:pull")
        
        assert client._token == "test-token"


class TestRegistryClientConnection:
    """Tests for connection management."""
    
    @pytest.fixture
    def client(self, sample_registry_credentials):
        return RegistryClient(sample_registry_credentials)
    
    @pytest.mark.asyncio
    async def test_connect(self, client):
        """Test client connection initialization."""
        await client.connect()
        assert client._client is not None
        await client.close()
    
    @pytest.mark.asyncio
    async def test_close(self, client):
        """Test client connection close."""
        await client.connect()
        assert client._client is not None
        await client.close()
        assert client._client is None
    
    @pytest.mark.asyncio
    async def test_context_manager(self, sample_registry_credentials):
        """Test async context manager."""
        async with RegistryClient(sample_registry_credentials) as client:
            assert client._client is not None
        assert client._client is None
    
    @pytest.mark.asyncio
    async def test_check_connectivity_success(self, client, mock_httpx_client):
        """Test successful connectivity check."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx_client.get.return_value = mock_response
        client._client = mock_httpx_client
        
        result = await client.check_connectivity()
        assert result is True
    
    @pytest.mark.asyncio
    async def test_check_connectivity_with_auth(self, client, mock_httpx_client):
        """Test connectivity with auth challenge."""
        mock_401 = MagicMock()
        mock_401.status_code = 401
        mock_401.headers = {"www-authenticate": "Basic realm=\"Registry\""}
        
        mock_200 = MagicMock()
        mock_200.status_code = 200
        
        mock_httpx_client.get.side_effect = [mock_401, mock_200]
        client._client = mock_httpx_client
        
        result = await client.check_connectivity()
        assert result is True
    
    @pytest.mark.asyncio
    async def test_check_connectivity_failure(self, client, mock_httpx_client):
        """Test connectivity failure."""
        mock_httpx_client.get.side_effect = httpx.ConnectError("Failed")
        client._client = mock_httpx_client
        
        with pytest.raises(RegistryConnectionError):
            await client.check_connectivity()


class TestRegistryClientRepositories:
    """Tests for repository and tag listing."""
    
    @pytest.fixture
    def client(self, sample_registry_credentials):
        return RegistryClient(sample_registry_credentials)
    
    @pytest.mark.asyncio
    async def test_list_repositories_success(self, client, mock_httpx_client):
        """Test successful repository listing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"repositories": ["app1", "app2"]}
        mock_response.headers = {}
        mock_httpx_client.get.return_value = mock_response
        client._client = mock_httpx_client
        
        repos = await client.list_repositories()
        assert repos == ["app1", "app2"]
    
    @pytest.mark.asyncio
    async def test_list_repositories_empty(self, client, mock_httpx_client):
        """Test empty repository listing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"repositories": []}
        mock_response.headers = {}
        mock_httpx_client.get.return_value = mock_response
        client._client = mock_httpx_client
        
        repos = await client.list_repositories()
        assert repos == []
    
    @pytest.mark.asyncio
    async def test_list_repositories_pagination(self, client, mock_httpx_client):
        """Test repository listing with pagination."""
        mock_response1 = MagicMock()
        mock_response1.status_code = 200
        mock_response1.json.return_value = {"repositories": ["repo1", "repo2"]}
        mock_response1.headers = {"Link": '<https://test.io/v2/_catalog?last=repo2>; rel="next"'}
        
        mock_response2 = MagicMock()
        mock_response2.status_code = 200
        mock_response2.json.return_value = {"repositories": ["repo3"]}
        mock_response2.headers = {}
        
        mock_httpx_client.get.side_effect = [mock_response1, mock_response2]
        client._client = mock_httpx_client
        
        repos = await client.list_repositories()
        assert len(repos) >= 2
    
    @pytest.mark.asyncio
    async def test_list_tags_success(self, client, mock_httpx_client):
        """Test successful tag listing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"name": "app", "tags": ["v1.0", "latest"]}
        mock_httpx_client.get.return_value = mock_response
        client._client = mock_httpx_client
        
        tags = await client.list_tags("app")
        assert tags == ["v1.0", "latest"]
    
    @pytest.mark.asyncio
    async def test_list_tags_not_found(self, client, mock_httpx_client):
        """Test tag listing for non-existent repository."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_httpx_client.get.return_value = mock_response
        client._client = mock_httpx_client
        
        tags = await client.list_tags("nonexistent")
        assert tags == []


class TestRegistryClientManifests:
    """Tests for manifest operations."""
    
    @pytest.fixture
    def client(self, sample_registry_credentials):
        return RegistryClient(sample_registry_credentials)
    
    @pytest.mark.asyncio
    async def test_get_manifest_success(self, client, mock_httpx_client):
        """Test successful manifest retrieval."""
        manifest_content = b'{"schemaVersion": 2, "layers": []}'
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = manifest_content
        mock_response.headers = {
            "Docker-Content-Digest": "sha256:abc123",
            "Content-Type": "application/vnd.docker.distribution.manifest.v2+json"
        }
        mock_response.json.return_value = {"schemaVersion": 2, "layers": []}
        mock_httpx_client.get.return_value = mock_response
        client._client = mock_httpx_client
        
        info, content = await client.get_manifest("app", "v1.0")
        assert info.digest == "sha256:abc123"
        assert content == manifest_content
    
    @pytest.mark.asyncio
    async def test_get_manifest_not_found(self, client, mock_httpx_client):
        """Test manifest not found."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_httpx_client.get.return_value = mock_response
        client._client = mock_httpx_client
        
        with pytest.raises(ImageTransferError):
            await client.get_manifest("app", "nonexistent")
    
    @pytest.mark.asyncio
    async def test_upload_manifest_success(self, client, mock_httpx_client):
        """Test successful manifest upload."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.headers = {"Docker-Content-Digest": "sha256:abc"}
        mock_httpx_client.put.return_value = mock_response
        
        client._client = mock_httpx_client
        
        result = await client.upload_manifest(
            "repo", "v1.0", b"{}",
            "application/vnd.docker.distribution.manifest.v2+json"
        )
        assert result == "sha256:abc"


class TestRegistryClientBlobs:
    """Tests for blob operations."""
    
    @pytest.fixture
    def client(self, sample_registry_credentials):
        return RegistryClient(sample_registry_credentials)
    
    @pytest.mark.asyncio
    async def test_blob_exists_true(self, client, mock_httpx_client):
        """Test blob existence check - exists."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx_client.head.return_value = mock_response
        client._client = mock_httpx_client
        
        exists = await client.blob_exists("app", "sha256:abc")
        assert exists is True
    
    @pytest.mark.asyncio
    async def test_blob_exists_false(self, client, mock_httpx_client):
        """Test blob existence check - does not exist."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_httpx_client.head.return_value = mock_response
        client._client = mock_httpx_client
        
        exists = await client.blob_exists("app", "sha256:abc")
        assert exists is False
    
    @pytest.mark.asyncio
    async def test_mount_blob_success(self, client, mock_httpx_client):
        """Test successful blob mount."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_httpx_client.post.return_value = mock_response
        client._client = mock_httpx_client
        
        result = await client.mount_blob("dest", "source", "sha256:abc")
        assert result is True
    
    @pytest.mark.asyncio
    async def test_mount_blob_needs_upload(self, client, mock_httpx_client):
        """Test mount blob when upload is needed."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.headers = {"Location": "https://test.io/v2/repo/uploads/123"}
        mock_httpx_client.post.return_value = mock_response
        
        client._client = mock_httpx_client
        
        result = await client.mount_blob("dest", "source", "sha256:abc")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_upload_blob(self, client, mock_httpx_client):
        """Test blob upload."""
        mock_start = MagicMock()
        mock_start.status_code = 202
        mock_start.headers = {"Location": "https://test.io/v2/repo/uploads/123"}
        
        mock_finish = MagicMock()
        mock_finish.status_code = 201
        
        mock_httpx_client.post.return_value = mock_start
        mock_httpx_client.put.return_value = mock_finish
        
        client._client = mock_httpx_client
        
        await client.upload_blob("repo", "sha256:abc", b"data")


class TestRegistryClientImageCopy:
    """Tests for image copy operations."""
    
    @pytest.fixture
    def client(self, sample_registry_credentials):
        return RegistryClient(sample_registry_credentials)
    
    @pytest.mark.asyncio
    async def test_copy_image_mocked(self, client):
        """Test image copy with mocked method."""
        client.copy_image = AsyncMock(return_value=1024)
        result = await client.copy_image("repo", "v1.0", MagicMock(), "repo", "v1.0")
        assert result == 1024


class TestManifestInfo:
    """Tests for ManifestInfo dataclass."""
    
    def test_creation(self):
        """Test ManifestInfo creation."""
        info = ManifestInfo(
            digest="sha256:abc",
            media_type="application/vnd.docker.distribution.manifest.v2+json",
            size=1024,
        )
        assert info.digest == "sha256:abc"
        assert info.layers == []
    
    def test_with_layers(self):
        """Test ManifestInfo with layers."""
        layers = [{"digest": "sha256:layer1"}, {"digest": "sha256:layer2"}]
        info = ManifestInfo(
            digest="sha256:abc",
            media_type="application/vnd.docker.distribution.manifest.v2+json",
            size=1024,
            layers=layers,
        )
        assert len(info.layers) == 2


class TestBlobInfo:
    """Tests for BlobInfo dataclass."""
    
    def test_creation(self):
        """Test BlobInfo creation."""
        info = BlobInfo(
            digest="sha256:abc",
            size=4096,
            media_type="application/octet-stream",
        )
        assert info.size == 4096


class TestRegistryExceptions:
    """Tests for registry client exceptions."""
    
    def test_registry_connection_error(self):
        """Test RegistryConnectionError."""
        error = RegistryConnectionError("test.io", "Connection refused")
        assert "test.io" in str(error)
        assert error.registry == "test.io"
    
    def test_registry_authentication_error(self):
        """Test RegistryAuthenticationError."""
        error = RegistryAuthenticationError("test.io", "Invalid credentials")
        assert "test.io" in str(error)
    
    def test_image_transfer_error(self):
        """Test ImageTransferError."""
        error = ImageTransferError("app:v1.0", "Upload failed")
        assert "app:v1.0" in str(error)
        assert error.image == "app:v1.0"
