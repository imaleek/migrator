"""
Unit tests for the config module.
"""
import pytest
from pydantic import ValidationError

from config import (
    RegistryType,
    RegistryCredentials,
    MigrationConfig,
    ImageReference,
    ChartReference,
    MigrationResult,
    MigrationSummary,
)


class TestRegistryCredentials:
    """Tests for RegistryCredentials model."""
    
    def test_valid_credentials(self):
        """Test creating valid credentials."""
        creds = RegistryCredentials(
            registry="docker.io",
            username="user",
            password="pass",
        )
        assert creds.registry == "docker.io"
        assert creds.username == "user"
        assert creds.password == "pass"
        assert creds.insecure is False
    
    def test_registry_url_normalization_removes_protocol(self):
        """Test that https:// prefix is removed."""
        creds = RegistryCredentials(
            registry="https://harbor.example.com",
            username="user",
            password="pass",
        )
        assert creds.registry == "harbor.example.com"
    
    def test_registry_url_normalization_removes_http(self):
        """Test that http:// prefix is removed."""
        creds = RegistryCredentials(
            registry="http://localhost:5000",
            username="user",
            password="pass",
        )
        assert creds.registry == "localhost:5000"
    
    def test_registry_url_removes_trailing_slash(self):
        """Test that trailing slash is removed."""
        creds = RegistryCredentials(
            registry="harbor.example.com/",
            username="user",
            password="pass",
        )
        assert creds.registry == "harbor.example.com"
    
    def test_invalid_registry_empty(self):
        """Test that empty registry raises error."""
        with pytest.raises(ValidationError):
            RegistryCredentials(
                registry="",
                username="user",
                password="pass",
            )
    
    def test_invalid_registry_with_space(self):
        """Test that registry with space raises error."""
        with pytest.raises(ValidationError):
            RegistryCredentials(
                registry="invalid registry",
                username="user",
                password="pass",
            )
    
    def test_registry_type_detection_docker_hub(self):
        """Test Docker Hub detection."""
        creds = RegistryCredentials(
            registry="docker.io",
            username="user",
            password="pass",
        )
        assert creds.registry_type == RegistryType.DOCKER_HUB
        
        creds2 = RegistryCredentials(
            registry="index.docker.io",
            username="user",
            password="pass",
        )
        assert creds2.registry_type == RegistryType.DOCKER_HUB
    
    def test_registry_type_detection_acr(self):
        """Test Azure Container Registry detection."""
        creds = RegistryCredentials(
            registry="myregistry.azurecr.io",
            username="user",
            password="pass",
        )
        assert creds.registry_type == RegistryType.ACR
    
    def test_registry_type_detection_ecr(self):
        """Test AWS ECR detection."""
        creds = RegistryCredentials(
            registry="123456789.dkr.ecr.us-east-1.amazonaws.com",
            username="user",
            password="pass",
        )
        assert creds.registry_type == RegistryType.ECR
    
    def test_registry_type_detection_gcr(self):
        """Test Google Container Registry detection."""
        creds = RegistryCredentials(
            registry="gcr.io/my-project",
            username="user",
            password="pass",
        )
        assert creds.registry_type == RegistryType.GCR
    
    def test_registry_type_detection_gar(self):
        """Test Google Artifact Registry detection."""
        creds = RegistryCredentials(
            registry="us-docker.pkg.dev",
            username="user",
            password="pass",
        )
        assert creds.registry_type == RegistryType.GAR
    
    def test_registry_type_detection_ghcr(self):
        """Test GitHub Container Registry detection."""
        creds = RegistryCredentials(
            registry="ghcr.io",
            username="user",
            password="pass",
        )
        assert creds.registry_type == RegistryType.GHCR
    
    def test_registry_type_detection_quay(self):
        """Test Quay.io detection."""
        creds = RegistryCredentials(
            registry="quay.io",
            username="user",
            password="pass",
        )
        assert creds.registry_type == RegistryType.QUAY
    
    def test_registry_type_detection_harbor(self):
        """Test Harbor detection."""
        creds = RegistryCredentials(
            registry="harbor.mycompany.com",
            username="user",
            password="pass",
        )
        assert creds.registry_type == RegistryType.HARBOR
    
    def test_registry_type_detection_generic(self):
        """Test generic registry detection."""
        creds = RegistryCredentials(
            registry="myregistry.example.com",
            username="user",
            password="pass",
        )
        assert creds.registry_type == RegistryType.GENERIC
    
    def test_api_base_url_https(self):
        """Test HTTPS API base URL."""
        creds = RegistryCredentials(
            registry="harbor.example.com",
            username="user",
            password="pass",
            insecure=False,
        )
        assert creds.api_base_url == "https://harbor.example.com/v2"
    
    def test_api_base_url_http(self):
        """Test HTTP API base URL for insecure registries."""
        creds = RegistryCredentials(
            registry="localhost:5000",
            username="user",
            password="pass",
            insecure=True,
        )
        assert creds.api_base_url == "http://localhost:5000/v2"


class TestMigrationConfig:
    """Tests for MigrationConfig model."""
    
    def test_valid_config(self, sample_migration_config):
        """Test creating valid migration config."""
        config = sample_migration_config
        assert config.parallel_jobs == 4
        assert config.dry_run is False
        assert config.skip_existing is True
    
    def test_default_values(self):
        """Test default configuration values."""
        source = RegistryCredentials(
            registry="source.io", username="u", password="p"
        )
        dest = RegistryCredentials(
            registry="dest.io", username="u", password="p"
        )
        config = MigrationConfig(source=source, destination=dest)
        
        assert config.parallel_jobs == 4
        assert config.dry_run is False
        assert config.skip_existing is True
        assert config.include_pattern is None
        assert config.exclude_pattern is None
        assert config.retry_attempts == 3
        assert config.retry_delay == 2.0
    
    def test_parallel_jobs_bounds(self):
        """Test parallel_jobs validation bounds."""
        source = RegistryCredentials(
            registry="source.io", username="u", password="p"
        )
        dest = RegistryCredentials(
            registry="dest.io", username="u", password="p"
        )
        
        # Too low
        with pytest.raises(ValidationError):
            MigrationConfig(source=source, destination=dest, parallel_jobs=0)
        
        # Too high
        with pytest.raises(ValidationError):
            MigrationConfig(source=source, destination=dest, parallel_jobs=25)
        
        # Valid bounds
        config_min = MigrationConfig(source=source, destination=dest, parallel_jobs=1)
        assert config_min.parallel_jobs == 1
        
        config_max = MigrationConfig(source=source, destination=dest, parallel_jobs=20)
        assert config_max.parallel_jobs == 20
    
    def test_valid_include_pattern(self):
        """Test valid include regex pattern."""
        source = RegistryCredentials(
            registry="source.io", username="u", password="p"
        )
        dest = RegistryCredentials(
            registry="dest.io", username="u", password="p"
        )
        config = MigrationConfig(
            source=source, destination=dest, include_pattern="^prod-.*"
        )
        assert config.include_pattern == "^prod-.*"
    
    def test_invalid_include_pattern(self):
        """Test invalid regex pattern raises error."""
        source = RegistryCredentials(
            registry="source.io", username="u", password="p"
        )
        dest = RegistryCredentials(
            registry="dest.io", username="u", password="p"
        )
        with pytest.raises(ValidationError):
            MigrationConfig(
                source=source, destination=dest, include_pattern="[invalid"
            )


class TestImageReference:
    """Tests for ImageReference model."""
    
    def test_basic_image_reference(self):
        """Test basic image reference creation."""
        ref = ImageReference(repository="library/nginx", tag="latest")
        assert ref.repository == "library/nginx"
        assert ref.tag == "latest"
        assert ref.digest is None
    
    def test_default_tag(self):
        """Test default tag is 'latest'."""
        ref = ImageReference(repository="myapp")
        assert ref.tag == "latest"
    
    def test_full_reference_with_tag(self):
        """Test full reference string with tag."""
        ref = ImageReference(repository="library/nginx", tag="1.21")
        assert ref.full_reference == "library/nginx:1.21"
    
    def test_full_reference_with_digest(self):
        """Test full reference string with digest."""
        digest = "sha256:abc123"
        ref = ImageReference(repository="library/nginx", digest=digest)
        assert ref.full_reference == f"library/nginx@{digest}"
    
    def test_with_registry(self):
        """Test adding registry prefix."""
        ref = ImageReference(repository="myapp", tag="v1.0")
        full = ref.with_registry("harbor.example.com")
        assert full == "harbor.example.com/myapp:v1.0"


class TestChartReference:
    """Tests for ChartReference model."""
    
    def test_basic_chart_reference(self):
        """Test basic chart reference creation."""
        ref = ChartReference(name="mychart", version="1.0.0")
        assert ref.name == "mychart"
        assert ref.version == "1.0.0"
        assert ref.repository is None
    
    def test_full_reference_without_repository(self):
        """Test full reference without repository prefix."""
        ref = ChartReference(name="mychart", version="1.0.0")
        assert ref.full_reference == "mychart:1.0.0"
    
    def test_full_reference_with_repository(self):
        """Test full reference with repository prefix."""
        ref = ChartReference(name="mychart", version="1.0.0", repository="charts")
        assert ref.full_reference == "charts/mychart:1.0.0"


class TestMigrationResult:
    """Tests for MigrationResult model."""
    
    def test_successful_result(self):
        """Test successful migration result."""
        result = MigrationResult(
            source="source.io/app:v1",
            destination="dest.io/app:v1",
            success=True,
            duration_seconds=5.5,
            size_bytes=1024000,
        )
        assert result.success is True
        assert result.error is None
        assert result.skipped is False
    
    def test_failed_result(self):
        """Test failed migration result."""
        result = MigrationResult(
            source="source.io/app:v1",
            destination="dest.io/app:v1",
            success=False,
            error="Connection timeout",
        )
        assert result.success is False
        assert result.error == "Connection timeout"
    
    def test_skipped_result(self):
        """Test skipped migration result."""
        result = MigrationResult(
            source="source.io/app:v1",
            destination="dest.io/app:v1",
            success=True,
            skipped=True,
        )
        assert result.skipped is True


class TestMigrationSummary:
    """Tests for MigrationSummary model."""
    
    def test_empty_summary(self):
        """Test empty migration summary."""
        summary = MigrationSummary()
        assert summary.total_items == 0
        assert summary.successful == 0
        assert summary.failed == 0
        assert summary.skipped == 0
        assert summary.success_rate == 100.0
    
    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        summary = MigrationSummary(
            total_items=10,
            successful=8,
            failed=2,
        )
        assert summary.success_rate == 80.0
    
    def test_success_rate_all_successful(self):
        """Test 100% success rate."""
        summary = MigrationSummary(
            total_items=5,
            successful=5,
            failed=0,
        )
        assert summary.success_rate == 100.0
    
    def test_success_rate_all_failed(self):
        """Test 0% success rate."""
        summary = MigrationSummary(
            total_items=5,
            successful=0,
            failed=5,
        )
        assert summary.success_rate == 0.0
