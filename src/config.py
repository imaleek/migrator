"""
Configuration models for the Migrator application.

Provides Pydantic models for type-safe configuration handling.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import re


class RegistryType(str, Enum):
    """Supported registry types for automatic configuration."""
    GENERIC = "generic"
    DOCKER_HUB = "dockerhub"
    HARBOR = "harbor"
    ACR = "acr"  # Azure Container Registry
    ECR = "ecr"  # AWS Elastic Container Registry
    GCR = "gcr"  # Google Container Registry
    GAR = "gar"  # Google Artifact Registry
    GHCR = "ghcr"  # GitHub Container Registry
    QUAY = "quay"


class RegistryCredentials(BaseModel):
    """Registry connection credentials."""
    
    registry: str = Field(..., description="Registry URL (e.g., registry.example.com)")
    username: str = Field(..., description="Registry username")
    password: str = Field(..., description="Registry password or token")
    insecure: bool = Field(default=False, description="Allow insecure HTTP connections")
    
    @field_validator("registry")
    @classmethod
    def validate_registry(cls, v: str) -> str:
        """Normalize and validate registry URL."""
        # Remove protocol prefix if present
        v = re.sub(r"^https?://", "", v)
        # Remove trailing slash
        v = v.rstrip("/")
        # Basic validation
        if not v or " " in v:
            raise ValueError("Invalid registry URL")
        return v
    
    @property
    def registry_type(self) -> RegistryType:
        """Detect registry type from URL."""
        registry_lower = self.registry.lower()
        
        if "docker.io" in registry_lower or "index.docker.io" in registry_lower:
            return RegistryType.DOCKER_HUB
        elif "azurecr.io" in registry_lower:
            return RegistryType.ACR
        elif "ecr" in registry_lower and "amazonaws.com" in registry_lower:
            return RegistryType.ECR
        elif "gcr.io" in registry_lower:
            return RegistryType.GCR
        elif "pkg.dev" in registry_lower:
            return RegistryType.GAR
        elif "ghcr.io" in registry_lower:
            return RegistryType.GHCR
        elif "quay.io" in registry_lower:
            return RegistryType.QUAY
        elif "harbor" in registry_lower:
            return RegistryType.HARBOR
        return RegistryType.GENERIC
    
    @property
    def api_base_url(self) -> str:
        """Get the base URL for Registry API v2."""
        protocol = "http" if self.insecure else "https"
        return f"{protocol}://{self.registry}/v2"
    
    def model_post_init(self, __context) -> None:
        """Post-initialization processing."""
        pass


class MigrationConfig(BaseModel):
    """Configuration for a migration operation."""
    
    source: RegistryCredentials = Field(..., description="Source registry credentials")
    destination: RegistryCredentials = Field(..., description="Destination registry credentials")
    parallel_jobs: int = Field(default=4, ge=1, le=20, description="Number of parallel migration jobs")
    dry_run: bool = Field(default=False, description="Simulate migration without making changes")
    skip_existing: bool = Field(default=True, description="Skip images that already exist in destination")
    include_pattern: Optional[str] = Field(default=None, description="Regex pattern to include repositories")
    exclude_pattern: Optional[str] = Field(default=None, description="Regex pattern to exclude repositories")
    retry_attempts: int = Field(default=3, ge=1, le=10, description="Number of retry attempts on failure")
    retry_delay: float = Field(default=2.0, ge=0.5, le=30.0, description="Delay between retries in seconds")
    
    @field_validator("include_pattern", "exclude_pattern")
    @classmethod
    def validate_pattern(cls, v: Optional[str]) -> Optional[str]:
        """Validate regex patterns."""
        if v is not None:
            try:
                re.compile(v)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern: {e}")
        return v


class ImageReference(BaseModel):
    """Reference to a container image."""
    
    repository: str = Field(..., description="Repository name (e.g., library/nginx)")
    tag: str = Field(default="latest", description="Image tag")
    digest: Optional[str] = Field(default=None, description="Image digest (sha256:...)")
    
    @property
    def full_reference(self) -> str:
        """Get full image reference string."""
        if self.digest:
            return f"{self.repository}@{self.digest}"
        return f"{self.repository}:{self.tag}"
    
    def with_registry(self, registry: str) -> str:
        """Get full image reference with registry prefix."""
        return f"{registry}/{self.full_reference}"


class ChartReference(BaseModel):
    """Reference to a Helm chart."""
    
    name: str = Field(..., description="Chart name")
    version: str = Field(..., description="Chart version")
    repository: Optional[str] = Field(default=None, description="Repository path prefix")
    
    @property
    def full_reference(self) -> str:
        """Get full chart reference string."""
        if self.repository:
            return f"{self.repository}/{self.name}:{self.version}"
        return f"{self.name}:{self.version}"


class MigrationResult(BaseModel):
    """Result of a single item migration."""
    
    source: str = Field(..., description="Source reference")
    destination: str = Field(..., description="Destination reference")
    success: bool = Field(..., description="Whether migration succeeded")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    duration_seconds: float = Field(default=0.0, description="Migration duration")
    size_bytes: Optional[int] = Field(default=None, description="Size of migrated content")
    skipped: bool = Field(default=False, description="Whether item was skipped")


class MigrationSummary(BaseModel):
    """Summary of a complete migration operation."""
    
    total_items: int = Field(default=0, description="Total items discovered")
    successful: int = Field(default=0, description="Successfully migrated items")
    failed: int = Field(default=0, description="Failed migrations")
    skipped: int = Field(default=0, description="Skipped items")
    total_duration_seconds: float = Field(default=0.0, description="Total operation duration")
    total_size_bytes: int = Field(default=0, description="Total data transferred")
    results: list[MigrationResult] = Field(default_factory=list, description="Individual results")
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_items == 0:
            return 100.0
        return (self.successful / self.total_items) * 100
