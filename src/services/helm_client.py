"""
Helm OCI Registry Client.

Provides Helm chart migration via Helm CLI with OCI registry support.
Requires Helm 3.8+ with OCI experimental features enabled.
"""
import asyncio
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from config import ChartReference, RegistryCredentials
from utilities.exceptions import MigratorError
from utilities.logger import get_logger

logger = get_logger(__name__)


class HelmNotFoundError(MigratorError):
    """Raised when Helm CLI is not found."""
    def __init__(self):
        super().__init__(
            "Helm CLI not found",
            "Please install Helm 3.8+ with OCI support: https://helm.sh/docs/intro/install/"
        )


class HelmChartError(MigratorError):
    """Raised when Helm chart operations fail."""
    def __init__(self, chart: str, operation: str, message: str):
        self.chart = chart
        self.operation = operation
        super().__init__(f"Helm {operation} failed for {chart}: {message}")


@dataclass
class ChartInfo:
    """Information about a Helm chart."""
    name: str
    version: str
    description: str = ""
    app_version: str = ""
    digest: str = ""
    created: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.name}:{self.version}"


class HelmClient:
    """
    Helm chart OCI registry client.

    Uses Helm CLI for OCI registry operations because:
    - Helm charts in OCI registries have specific packaging requirements
    - Helm CLI handles chart validation and metadata
    - Authentication is managed by Helm's credential store

    Requires Helm 3.8+ with OCI support.
    """

    def __init__(self, credentials: RegistryCredentials):
        """
        Initialize the Helm client.

        Args:
            credentials: Registry credentials
        """
        self.credentials = credentials
        self._helm_path: str | None = None
        self._temp_dir: Path | None = None
        self._logged_in = False

    @property
    def registry(self) -> str:
        """Get the registry hostname."""
        return self.credentials.registry

    @property
    def oci_url(self) -> str:
        """Get the OCI URL prefix."""
        return f"oci://{self.registry}"

    async def __aenter__(self) -> "HelmClient":
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.cleanup()

    async def initialize(self) -> None:
        """Initialize the Helm client and verify Helm is available."""
        self._helm_path = shutil.which("helm")
        if not self._helm_path:
            raise HelmNotFoundError()

        # Verify Helm version
        version = await self._run_helm(["version", "--short"])
        logger.debug(f"Found Helm: {version}")

        # Check for OCI support (Helm 3.8+)
        match = re.search(r"v(\d+)\.(\d+)", version)
        if match:
            major, minor = int(match.group(1)), int(match.group(2))
            if major < 3 or (major == 3 and minor < 8):
                raise MigratorError(
                    f"Helm {version} does not support OCI registries",
                    "Please upgrade to Helm 3.8 or later"
                )

        # Create temp directory for chart operations
        self._temp_dir = Path(tempfile.mkdtemp(prefix="helm_migration_"))
        logger.debug(f"Created temp directory: {self._temp_dir}")

    async def cleanup(self) -> None:
        """Clean up resources."""
        # Logout from registry
        if self._logged_in:
            try:
                await self._run_helm(
                    ["registry", "logout", self.registry],
                    check=False
                )
            except Exception:
                pass

        # Remove temp directory
        if self._temp_dir and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            logger.debug(f"Cleaned up temp directory: {self._temp_dir}")

    async def _run_helm(
        self,
        args: list[str],
        check: bool = True,
        capture_output: bool = True
    ) -> str:
        """
        Run a Helm command.

        Args:
            args: Command arguments
            check: Raise on non-zero exit
            capture_output: Capture stdout/stderr

        Returns:
            Command output
        """
        cmd = [self._helm_path] + args
        logger.debug(f"Running: {' '.join(cmd)}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE if capture_output else None,
            stderr=asyncio.subprocess.PIPE if capture_output else None,
            env={**os.environ, "HELM_EXPERIMENTAL_OCI": "1"},
        )

        stdout, stderr = await process.communicate()

        if check and process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise MigratorError(
                f"Helm command failed: {' '.join(args)}",
                error_msg
            )

        return stdout.decode().strip() if stdout else ""

    async def login(self) -> bool:
        """
        Login to the OCI registry.

        Returns:
            True if login succeeded
        """
        if self._logged_in:
            return True

        try:
            # Use --password-stdin for security
            process = await asyncio.create_subprocess_exec(
                self._helm_path,
                "registry", "login",
                self.registry,
                "--username", self.credentials.username,
                "--password-stdin",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HELM_EXPERIMENTAL_OCI": "1"},
            )

            stdout, stderr = await process.communicate(
                input=self.credentials.password.encode()
            )

            if process.returncode == 0:
                self._logged_in = True
                logger.info(f"Logged in to Helm registry: {self.registry}")
                return True
            else:
                error = stderr.decode() if stderr else "Unknown error"
                logger.error(f"Helm login failed: {error}")
                return False

        except Exception as e:
            logger.error(f"Helm login error: {e}")
            return False

    async def list_charts(self, repository: str | None = None) -> list[ChartInfo]:
        """
        List charts in the registry.

        Note: OCI registries don't have a standard API for listing charts.
        This method attempts to use registry-specific APIs where available.

        Args:
            repository: Optional repository path prefix

        Returns:
            List of chart information
        """
        # OCI registries don't have a standard catalog API for Helm charts
        # We'll use the same approach as container images - query the registry API
        # and filter for Helm chart media types

        charts = []

        # This is a limitation - we can't easily list OCI charts without
        # registry-specific APIs (Harbor, ACR, etc.)
        # For now, return empty and rely on user providing chart list
        # or implementing registry-specific listing

        logger.warning(
            "OCI chart listing is not standardized. "
            "Consider providing explicit chart list or using registry-specific APIs."
        )

        return charts

    async def pull_chart(
        self,
        chart_ref: ChartReference,
        dest_dir: Path | None = None
    ) -> Path:
        """
        Pull a chart from the registry.

        Args:
            chart_ref: Chart reference
            dest_dir: Destination directory (uses temp if not specified)

        Returns:
            Path to the downloaded chart archive
        """
        if not self._logged_in:
            await self.login()

        dest = dest_dir or self._temp_dir

        # Build OCI reference
        if chart_ref.repository:
            oci_ref = f"{self.oci_url}/{chart_ref.repository}/{chart_ref.name}"
        else:
            oci_ref = f"{self.oci_url}/{chart_ref.name}"

        # Pull the chart
        await self._run_helm([
            "pull", oci_ref,
            "--version", chart_ref.version,
            "--destination", str(dest),
        ])

        # Find the downloaded file
        chart_file = dest / f"{chart_ref.name}-{chart_ref.version}.tgz"
        if not chart_file.exists():
            # Try to find it
            for f in dest.glob(f"{chart_ref.name}*.tgz"):
                chart_file = f
                break

        if not chart_file.exists():
            raise HelmChartError(
                chart_ref.full_reference,
                "pull",
                "Chart file not found after pull"
            )

        logger.debug(f"Pulled chart: {chart_file}")
        return chart_file

    async def push_chart(
        self,
        chart_path: Path,
        repository: str | None = None
    ) -> str:
        """
        Push a chart to the registry.

        Args:
            chart_path: Path to chart archive
            repository: Optional repository path prefix

        Returns:
            Full OCI reference of pushed chart
        """
        if not self._logged_in:
            await self.login()

        # Build destination reference
        if repository:
            dest_ref = f"{self.oci_url}/{repository}"
        else:
            dest_ref = self.oci_url

        # Push the chart
        output = await self._run_helm([
            "push", str(chart_path), dest_ref
        ])

        logger.debug(f"Pushed chart to {dest_ref}")

        # Extract full reference from output
        match = re.search(r"Pushed:\s+(.+)", output)
        if match:
            return match.group(1)

        return f"{dest_ref}/{chart_path.stem}"

    async def copy_chart(
        self,
        chart_ref: ChartReference,
        dest_client: "HelmClient",
        dest_repository: str | None = None
    ) -> int:
        """
        Copy a chart to another registry.

        Args:
            chart_ref: Source chart reference
            dest_client: Destination Helm client
            dest_repository: Destination repository path

        Returns:
            Size of transferred chart in bytes
        """
        # Pull from source
        chart_path = await self.pull_chart(chart_ref)

        # Get size
        size = chart_path.stat().st_size

        # Push to destination
        await dest_client.push_chart(chart_path, dest_repository)

        logger.info(f"Migrated chart: {chart_ref.full_reference}")

        return size

    async def get_chart_info(self, chart_path: Path) -> ChartInfo:
        """
        Extract chart information from archive.

        Args:
            chart_path: Path to chart archive

        Returns:
            Chart information
        """
        output = await self._run_helm([
            "show", "chart", str(chart_path)
        ])

        # Parse YAML output
        import yaml
        chart_data = yaml.safe_load(output)

        return ChartInfo(
            name=chart_data.get("name", ""),
            version=chart_data.get("version", ""),
            description=chart_data.get("description", ""),
            app_version=chart_data.get("appVersion", ""),
        )
