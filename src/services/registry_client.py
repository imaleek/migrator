"""
Docker Registry HTTP API v2 Client.

Provides direct HTTP-based access to container registries without requiring
Docker daemon. This is the most efficient approach for registry-to-registry
transfers as it avoids local storage and Docker overhead.

Supports:
- Docker Hub
- Harbor
- Azure Container Registry (ACR)
- Amazon ECR
- Google Container Registry (GCR)
- GitHub Container Registry (GHCR)
- Any OCI-compliant registry
"""
import base64
import hashlib
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import RegistryCredentials
from utilities.exceptions import MigratorError
from utilities.logger import get_logger

logger = get_logger(__name__)


class RegistryConnectionError(MigratorError):
    """Raised when registry connection fails."""
    def __init__(self, registry: str, message: str, details: str | None = None):
        self.registry = registry
        super().__init__(f"Failed to connect to {registry}: {message}", details)


class RegistryAuthenticationError(MigratorError):
    """Raised when registry authentication fails."""
    def __init__(self, registry: str, message: str = "Authentication failed"):
        self.registry = registry
        super().__init__(f"Authentication failed for {registry}: {message}")


class ImageTransferError(MigratorError):
    """Raised when image transfer fails."""
    def __init__(self, image: str, message: str, details: str | None = None):
        self.image = image
        super().__init__(f"Failed to transfer {image}: {message}", details)


@dataclass
class ManifestInfo:
    """Container image manifest information."""
    digest: str
    media_type: str
    size: int
    config_digest: str | None = None
    layers: list[dict[str, Any]] = None

    def __post_init__(self):
        if self.layers is None:
            self.layers = []


@dataclass
class BlobInfo:
    """Container image blob information."""
    digest: str
    size: int
    media_type: str


class RegistryClient:
    """
    HTTP-based container registry client using Registry API v2.

    This client directly communicates with container registries over HTTP,
    providing the most efficient way to copy images between registries
    without requiring a local Docker daemon.

    Features:
    - Direct blob streaming (no local storage)
    - Parallel layer transfers
    - Cross-repository blob mounting
    - Chunked uploads for large layers
    - Automatic authentication handling
    """

    # Supported manifest media types
    MANIFEST_TYPES = [
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
    ]

    # Chunk size for uploads (5MB)
    CHUNK_SIZE = 5 * 1024 * 1024

    def __init__(
        self,
        credentials: RegistryCredentials,
        timeout: float = 300.0,
    ):
        """
        Initialize the registry client.

        Args:
            credentials: Registry credentials
            timeout: HTTP timeout in seconds
        """
        self.credentials = credentials
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._token_expiry: float = 0

    @property
    def base_url(self) -> str:
        """Get the base URL for API requests."""
        url = self.credentials.api_base_url
        # Defensive check: ensure URL has protocol
        if not url.startswith(("http://", "https://")):
            protocol = "http" if self.credentials.insecure else "https"
            url = f"{protocol}://{self.credentials.registry_host}/v2"
            logger.warning(f"Fixed base_url missing protocol: {url}")
        return url

    @property
    def registry(self) -> str:
        """Get the registry hostname."""
        return self.credentials.registry

    async def __aenter__(self) -> "RegistryClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()

    async def connect(self) -> None:
        """Initialize the HTTP client connection."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=True,
            verify=not self.credentials.insecure,
        )
        logger.debug(f"Connected to registry: {self.registry}")

    async def close(self) -> None:
        """Close the HTTP client connection."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.debug(f"Disconnected from registry: {self.registry}")

    def _get_auth_header(self) -> dict[str, str]:
        """Get the authorization header."""
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}

        # Basic auth fallback
        auth_string = f"{self.credentials.username}:{self.credentials.password}"
        encoded = base64.b64encode(auth_string.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    async def _handle_auth_challenge(
        self,
        response: httpx.Response,
        scope: str = "repository:library/alpine:pull"
    ) -> None:
        """Handle WWW-Authenticate challenge and obtain token."""
        www_auth = response.headers.get("www-authenticate", "")

        if not www_auth.lower().startswith("bearer"):
            # Basic auth is accepted, no token needed
            logger.debug("Registry accepts basic auth")
            return

        # Parse Bearer challenge
        # Format: Bearer realm="...",service="...",scope="..."
        params = {}
        for match in re.finditer(r'(\w+)="([^"]*)"', www_auth):
            params[match.group(1)] = match.group(2)

        realm = params.get("realm")
        service = params.get("service", "")

        if not realm:
            logger.warning("No realm in auth challenge")
            return

        # Request token
        token_url = realm
        token_params = {
            "service": service,
            "scope": scope,
        }

        logger.debug(f"Requesting token from {realm}")

        auth = (self.credentials.username, self.credentials.password)
        token_response = await self._client.get(
            token_url,
            params=token_params,
            auth=auth,
        )

        if token_response.status_code == 200:
            data = token_response.json()
            self._token = data.get("token") or data.get("access_token")
            logger.debug("Obtained auth token")
        else:
            raise RegistryAuthenticationError(
                self.registry,
                f"Token request failed: {token_response.status_code}"
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    async def check_connectivity(self) -> bool:
        """
        Check if the registry is accessible.

        Returns:
            True if registry is accessible

        Raises:
            RegistryConnectionError: If connection fails
        """
        try:
            response = await self._client.get(
                f"{self.base_url}/",
                headers=self._get_auth_header(),
            )

            if response.status_code == 401:
                # Need to authenticate
                await self._handle_auth_challenge(response)
                response = await self._client.get(
                    f"{self.base_url}/",
                    headers=self._get_auth_header(),
                )

            if response.status_code == 200:
                logger.info(f"Successfully connected to {self.registry}")
                return True
            else:
                raise RegistryConnectionError(
                    self.registry,
                    f"Unexpected status: {response.status_code}"
                )

        except httpx.TransportError as e:
            raise RegistryConnectionError(
                self.registry,
                str(e)
            ) from e

    async def list_repositories(self, limit: int = 1000) -> list[str]:
        """
        List all repositories in the registry.

        Args:
            limit: Maximum number of repositories to return

        Returns:
            List of repository names
        """
        repositories = []
        url = f"{self.base_url}/_catalog"
        params = {"n": min(limit, 100)}

        # Debug: Log the URL being used
        logger.debug(f"Listing repositories from: {url}")

        while url and len(repositories) < limit:
            # Ensure URL has protocol (fix for pagination URLs)
            if url and not url.startswith(("http://", "https://")):
                protocol = "http" if self.credentials.insecure else "https"
                url = f"{protocol}://{self.credentials.registry_host}{url}"
                logger.debug(f"Fixed URL with protocol: {url}")

            response = await self._client.get(
                url,
                params=params,
                headers=self._get_auth_header(),
            )

            if response.status_code == 401:
                await self._handle_auth_challenge(
                    response,
                    "registry:catalog:*"
                )
                continue

            if response.status_code != 200:
                logger.warning(f"Failed to list repositories: {response.status_code}")
                break

            data = response.json()
            repositories.extend(data.get("repositories", []))

            # Check for pagination
            link = response.headers.get("Link", "")
            if "rel=\"next\"" in link:
                # Extract next URL from Link header
                match = re.search(r'<([^>]+)>', link)
                if match:
                    url = match.group(1)
                    params = {}
                else:
                    break
            else:
                break

        logger.debug(f"Found {len(repositories)} repositories")
        return repositories[:limit]

    async def list_tags(self, repository: str) -> list[str]:
        """
        List all tags for a repository.

        Args:
            repository: Repository name

        Returns:
            List of tag names
        """
        url = f"{self.base_url}/{quote(repository, safe='')}/tags/list"

        response = await self._client.get(
            url,
            headers=self._get_auth_header(),
        )

        if response.status_code == 401:
            await self._handle_auth_challenge(
                response,
                f"repository:{repository}:pull"
            )
            response = await self._client.get(
                url,
                headers=self._get_auth_header(),
            )

        if response.status_code == 404:
            logger.debug(f"Repository not found: {repository}")
            return []

        if response.status_code != 200:
            logger.warning(f"Failed to list tags for {repository}: {response.status_code}")
            return []

        data = response.json()
        tags = data.get("tags") or []

        logger.debug(f"Found {len(tags)} tags for {repository}")
        return tags

    async def get_manifest(
        self,
        repository: str,
        reference: str
    ) -> tuple[ManifestInfo, bytes]:
        """
        Get the manifest for an image.

        Args:
            repository: Repository name
            reference: Tag or digest

        Returns:
            Tuple of (ManifestInfo, raw_manifest_bytes)
        """
        url = f"{self.base_url}/{quote(repository, safe='')}/manifests/{reference}"

        headers = self._get_auth_header()
        headers["Accept"] = ", ".join(self.MANIFEST_TYPES)

        response = await self._client.get(url, headers=headers)

        if response.status_code == 401:
            await self._handle_auth_challenge(
                response,
                f"repository:{repository}:pull"
            )
            response = await self._client.get(url, headers=headers)

        if response.status_code != 200:
            raise ImageTransferError(
                f"{repository}:{reference}",
                f"Failed to get manifest: {response.status_code}"
            )

        content = response.content
        digest = response.headers.get(
            "Docker-Content-Digest",
            f"sha256:{hashlib.sha256(content).hexdigest()}"
        )
        media_type = response.headers.get("Content-Type", self.MANIFEST_TYPES[0])

        manifest_data = response.json()

        # Extract layer information
        layers = []
        config_digest = None

        if "layers" in manifest_data:
            layers = manifest_data["layers"]
        if "config" in manifest_data:
            config_digest = manifest_data["config"].get("digest")

        info = ManifestInfo(
            digest=digest,
            media_type=media_type,
            size=len(content),
            config_digest=config_digest,
            layers=layers,
        )

        logger.debug(f"Got manifest {digest} for {repository}:{reference}")
        return info, content

    async def blob_exists(self, repository: str, digest: str) -> bool:
        """
        Check if a blob exists in the repository.

        Args:
            repository: Repository name
            digest: Blob digest

        Returns:
            True if blob exists
        """
        url = f"{self.base_url}/{quote(repository, safe='')}/blobs/{digest}"

        response = await self._client.head(
            url,
            headers=self._get_auth_header(),
        )

        if response.status_code == 401:
            await self._handle_auth_challenge(
                response,
                f"repository:{repository}:pull"
            )
            response = await self._client.head(
                url,
                headers=self._get_auth_header(),
            )

        return response.status_code == 200

    async def stream_blob(
        self,
        repository: str,
        digest: str
    ) -> AsyncIterator[bytes]:
        """
        Stream a blob's content.

        Args:
            repository: Repository name
            digest: Blob digest

        Yields:
            Chunks of blob data
        """
        url = f"{self.base_url}/{quote(repository, safe='')}/blobs/{digest}"

        async with self._client.stream(
            "GET",
            url,
            headers=self._get_auth_header(),
        ) as response:
            if response.status_code != 200:
                raise ImageTransferError(
                    f"{repository}@{digest}",
                    f"Failed to download blob: {response.status_code}"
                )

            async for chunk in response.aiter_bytes(chunk_size=self.CHUNK_SIZE):
                yield chunk

    async def mount_blob(
        self,
        dest_repository: str,
        source_repository: str,
        digest: str
    ) -> bool:
        """
        Mount a blob from another repository (cross-repo mount).

        This is an optimization that avoids re-uploading blobs that
        already exist in another repository on the same registry.

        Args:
            dest_repository: Destination repository
            source_repository: Source repository containing the blob
            digest: Blob digest

        Returns:
            True if mount succeeded
        """
        url = f"{self.base_url}/{quote(dest_repository, safe='')}/blobs/uploads/"
        params = {
            "mount": digest,
            "from": source_repository,
        }

        response = await self._client.post(
            url,
            params=params,
            headers=self._get_auth_header(),
        )

        if response.status_code == 401:
            await self._handle_auth_challenge(
                response,
                f"repository:{dest_repository}:push repository:{source_repository}:pull"
            )
            response = await self._client.post(
                url,
                params=params,
                headers=self._get_auth_header(),
            )

        # 201 = mounted successfully, 202 = need to upload
        return response.status_code == 201

    async def upload_blob(
        self,
        repository: str,
        digest: str,
        data: bytes,
        content_type: str = "application/octet-stream"
    ) -> None:
        """
        Upload a blob in a single request (monolithic upload).

        Args:
            repository: Repository name
            digest: Expected blob digest
            data: Blob content
            content_type: MIME type
        """
        # Start upload session
        url = f"{self.base_url}/{quote(repository, safe='')}/blobs/uploads/"

        response = await self._client.post(
            url,
            headers=self._get_auth_header(),
        )

        if response.status_code == 401:
            await self._handle_auth_challenge(
                response,
                f"repository:{repository}:push"
            )
            response = await self._client.post(
                url,
                headers=self._get_auth_header(),
            )

        if response.status_code not in (200, 202):
            raise ImageTransferError(
                f"{repository}@{digest}",
                f"Failed to start upload: {response.status_code}"
            )

        # Get upload URL
        upload_url = response.headers.get("Location")
        if not upload_url:
            raise ImageTransferError(
                f"{repository}@{digest}",
                "No upload URL in response"
            )

        # Make URL absolute if needed
        if not upload_url.startswith("http"):
            upload_url = urljoin(self.base_url, upload_url)

        # Add digest parameter
        separator = "&" if "?" in upload_url else "?"
        upload_url = f"{upload_url}{separator}digest={digest}"

        # Upload blob
        headers = self._get_auth_header()
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(data))

        response = await self._client.put(
            upload_url,
            content=data,
            headers=headers,
        )

        if response.status_code != 201:
            raise ImageTransferError(
                f"{repository}@{digest}",
                f"Failed to upload blob: {response.status_code}"
            )

        logger.debug(f"Uploaded blob {digest} to {repository}")

    async def upload_blob_stream(
        self,
        repository: str,
        digest: str,
        data_stream: AsyncIterator[bytes],
        total_size: int
    ) -> None:
        """
        Upload a blob using chunked transfer.

        Args:
            repository: Repository name
            digest: Expected blob digest
            data_stream: Async iterator of data chunks
            total_size: Total size of data
        """
        # Start upload session
        url = f"{self.base_url}/{quote(repository, safe='')}/blobs/uploads/"

        response = await self._client.post(
            url,
            headers=self._get_auth_header(),
        )

        if response.status_code == 401:
            await self._handle_auth_challenge(
                response,
                f"repository:{repository}:push"
            )
            response = await self._client.post(
                url,
                headers=self._get_auth_header(),
            )

        if response.status_code not in (200, 202):
            raise ImageTransferError(
                f"{repository}@{digest}",
                f"Failed to start upload: {response.status_code}"
            )

        upload_url = response.headers.get("Location")
        if not upload_url.startswith("http"):
            upload_url = urljoin(self.base_url, upload_url)

        # Stream chunks
        offset = 0
        async for chunk in data_stream:
            chunk_size = len(chunk)

            headers = self._get_auth_header()
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(chunk_size)
            headers["Content-Range"] = f"{offset}-{offset + chunk_size - 1}"

            response = await self._client.patch(
                upload_url,
                content=chunk,
                headers=headers,
            )

            if response.status_code not in (202, 204):
                raise ImageTransferError(
                    f"{repository}@{digest}",
                    f"Chunk upload failed: {response.status_code}"
                )

            upload_url = response.headers.get("Location", upload_url)
            if not upload_url.startswith("http"):
                upload_url = urljoin(self.base_url, upload_url)

            offset += chunk_size

        # Finalize upload
        separator = "&" if "?" in upload_url else "?"
        final_url = f"{upload_url}{separator}digest={digest}"

        response = await self._client.put(
            final_url,
            headers=self._get_auth_header(),
        )

        if response.status_code != 201:
            raise ImageTransferError(
                f"{repository}@{digest}",
                f"Failed to finalize upload: {response.status_code}"
            )

        logger.debug(f"Uploaded blob {digest} to {repository}")

    async def upload_manifest(
        self,
        repository: str,
        reference: str,
        manifest: bytes,
        media_type: str
    ) -> str:
        """
        Upload an image manifest.

        Args:
            repository: Repository name
            reference: Tag or digest
            manifest: Manifest content
            media_type: Manifest media type

        Returns:
            Manifest digest
        """
        url = f"{self.base_url}/{quote(repository, safe='')}/manifests/{reference}"

        headers = self._get_auth_header()
        headers["Content-Type"] = media_type

        response = await self._client.put(
            url,
            content=manifest,
            headers=headers,
        )

        if response.status_code == 401:
            await self._handle_auth_challenge(
                response,
                f"repository:{repository}:push"
            )
            response = await self._client.put(
                url,
                content=manifest,
                headers=headers,
            )

        if response.status_code not in (200, 201):
            raise ImageTransferError(
                f"{repository}:{reference}",
                f"Failed to upload manifest: {response.status_code}"
            )

        digest = response.headers.get(
            "Docker-Content-Digest",
            f"sha256:{hashlib.sha256(manifest).hexdigest()}"
        )

        logger.debug(f"Uploaded manifest {digest} to {repository}:{reference}")
        return digest

    async def copy_image(
        self,
        source_repo: str,
        source_ref: str,
        dest_client: "RegistryClient",
        dest_repo: str,
        dest_ref: str,
        on_progress: callable = None
    ) -> int:
        """
        Copy an image to another registry.

        This is the main high-level method for copying images.
        It handles manifest and all layers efficiently.

        Args:
            source_repo: Source repository
            source_ref: Source tag/digest
            dest_client: Destination registry client
            dest_repo: Destination repository
            dest_ref: Destination tag
            on_progress: Optional callback for progress updates

        Returns:
            Total bytes transferred
        """
        total_bytes = 0

        # Get source manifest
        manifest_info, manifest_data = await self.get_manifest(source_repo, source_ref)

        # Handle manifest list (multi-arch)
        if manifest_info.media_type in (
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.index.v1+json"
        ):
            # Copy each platform manifest
            manifest_list = json.loads(manifest_data)
            for idx, platform_manifest in enumerate(manifest_list.get("manifests", [])):
                platform_digest = platform_manifest["digest"]
                platform_bytes = await self.copy_image(
                    source_repo, platform_digest,
                    dest_client, dest_repo, platform_digest,
                    on_progress
                )
                total_bytes += platform_bytes
                if on_progress:
                    on_progress(f"Platform {idx + 1}/{len(manifest_list['manifests'])}")
        else:
            # Copy config blob
            if manifest_info.config_digest:
                if not await dest_client.blob_exists(dest_repo, manifest_info.config_digest):
                    config_data = b""
                    async for chunk in self.stream_blob(source_repo, manifest_info.config_digest):
                        config_data += chunk
                    await dest_client.upload_blob(
                        dest_repo,
                        manifest_info.config_digest,
                        config_data,
                        "application/vnd.docker.container.image.v1+json"
                    )
                    total_bytes += len(config_data)

            # Copy layers
            for layer in manifest_info.layers:
                layer_digest = layer["digest"]
                layer.get("size", 0)

                # Check if layer exists
                if await dest_client.blob_exists(dest_repo, layer_digest):
                    logger.debug(f"Layer {layer_digest} already exists, skipping")
                    continue

                # Try to mount from same registry (optimization)
                if self.registry == dest_client.registry:
                    if await dest_client.mount_blob(dest_repo, source_repo, layer_digest):
                        logger.debug(f"Mounted layer {layer_digest}")
                        continue

                # Stream copy the layer
                layer_data = b""
                async for chunk in self.stream_blob(source_repo, layer_digest):
                    layer_data += chunk

                await dest_client.upload_blob(
                    dest_repo,
                    layer_digest,
                    layer_data,
                    layer.get("mediaType", "application/octet-stream")
                )
                total_bytes += len(layer_data)

                if on_progress:
                    on_progress(f"Layer {layer_digest[:12]}")

        # Upload manifest
        await dest_client.upload_manifest(
            dest_repo,
            dest_ref,
            manifest_data,
            manifest_info.media_type
        )
        total_bytes += len(manifest_data)

        return total_bytes
