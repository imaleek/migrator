"""
Migrator - CLI Entry Point

A modular CLI application for migrating data between systems.
Supports container registry migration with extensible architecture for future migrations.
"""
import asyncio
import logging
import shutil
import sys

import typer
from rich.console import Console
from rich.panel import Panel

from config import MigrationConfig, RegistryCredentials
from console import MigrationConsole
from migrators.container_registry import ContainerRegistryMigrator
from utilities.logger import get_logger, set_global_level

# Initialize Typer app with rich help
app = typer.Typer(
    name="migrator",
    help="🚀  Migrator - Efficient data migration between systems",
    add_completion=True,
    rich_markup_mode="rich",
    no_args_is_help=True,
)

# Migrate command group
migrate_app = typer.Typer(
    name="migrate",
    help="Migration commands for different data types",
    no_args_is_help=True,
)
app.add_typer(migrate_app, name="migrate")

console = Console()
logger = get_logger(__name__)


def version_callback(value: bool):
    """Display version information."""
    if value:
        console.print(Panel.fit(
            "[bold cyan]Migrator[/bold cyan] v1.0.0\n"
            "[dim]Container Registry Migration Tool[/dim]",
            border_style="cyan"
        ))
        raise typer.Exit()


def check_tools() -> dict[str, bool]:
    """Check for required tools availability."""
    tools = {
        "docker": shutil.which("docker") is not None,
        "helm": shutil.which("helm") is not None,
    }
    return tools


@app.callback()
def main(
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Show version information",
    ),
):
    """
    🚀 [bold cyan]Migrator[/bold cyan] - Efficient data migration between systems.

    A modular CLI tool for migrating:

    • Container images between registries
    • Helm charts between OCI registries
    • More coming soon...

    [dim]Use --help on any command for more details.[/dim]
    """
    pass


@migrate_app.command(name="container-registry")
def migrate_container_registry(
    # Source registry options
    source_registry: str = typer.Option(
        ...,
        "--source-registry",
        "-sr",
        help="Source registry URL (e.g., source.registry.io)",
        rich_help_panel="Source Registry",
    ),
    source_user: str = typer.Option(
        ...,
        "--source-user",
        "-su",
        help="Source registry username",
        rich_help_panel="Source Registry",
    ),
    source_password: str = typer.Option(
        ...,
        "--source-password",
        "-sp",
        help="Source registry password or token",
        hide_input=True,
        rich_help_panel="Source Registry",
    ),
    source_insecure: bool = typer.Option(
        False,
        "--source-insecure",
        help="Allow insecure HTTP connection to source",
        rich_help_panel="Source Registry",
    ),

    # Destination registry options
    destination_registry: str = typer.Option(
        ...,
        "--destination-registry",
        "-dr",
        help="Destination registry URL (e.g., dest.registry.io)",
        rich_help_panel="Destination Registry",
    ),
    destination_user: str = typer.Option(
        ...,
        "--destination-user",
        "-du",
        help="Destination registry username",
        rich_help_panel="Destination Registry",
    ),
    destination_password: str = typer.Option(
        ...,
        "--destination-password",
        "-dp",
        help="Destination registry password or token",
        hide_input=True,
        rich_help_panel="Destination Registry",
    ),
    destination_insecure: bool = typer.Option(
        False,
        "--destination-insecure",
        help="Allow insecure HTTP connection to destination",
        rich_help_panel="Destination Registry",
    ),

    # Migration options
    parallel: int = typer.Option(
        4,
        "--parallel",
        "-p",
        min=1,
        max=20,
        help="Number of parallel migration jobs",
        rich_help_panel="Migration Options",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Simulate migration without making changes",
        rich_help_panel="Migration Options",
    ),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--no-skip-existing",
        help="Skip images that already exist in destination",
        rich_help_panel="Migration Options",
    ),
    include: str | None = typer.Option(
        None,
        "--include",
        "-i",
        help="Regex pattern to include repositories",
        rich_help_panel="Filtering",
    ),
    exclude: str | None = typer.Option(
        None,
        "--exclude",
        "-e",
        help="Regex pattern to exclude repositories",
        rich_help_panel="Filtering",
    ),

    # Output options
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output",
        rich_help_panel="Output",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable debug logging",
        rich_help_panel="Output",
    ),
):
    """
    🐳 [bold]Migrate container images and Helm charts between registries.[/bold]

    Efficiently copies all container images and Helm charts from a source
    registry to a destination registry using direct registry-to-registry transfers.

    [bold cyan]Features:[/bold cyan]
    • Direct HTTP API v2 transfer (no Docker daemon required for images)
    • Parallel migration for maximum speed
    • Skip existing images to resume interrupted migrations
    • Pattern-based filtering for selective migration
    • Support for any OCI-compliant registry

    [bold yellow]Examples:[/bold yellow]

    [dim]# Basic migration[/dim]
    $ migrator migrate container-registry \\
        --source-registry harbor.old.io \\
        --source-user admin \\
        --source-password secret \\
        --destination-registry harbor.new.io \\
        --destination-user admin \\
        --destination-password secret

    [dim]# Dry run with filtering[/dim]
    $ migrator migrate container-registry \\
        --source-registry gcr.io/project \\
        --source-user _json_key \\
        --source-password "$(cat key.json)" \\
        --destination-registry myregistry.azurecr.io \\
        --destination-user myuser \\
        --destination-password mytoken \\
        --include "^prod-" \\
        --exclude ".*-dev$" \\
        --dry-run
    """
    # Set up logging
    if debug:
        set_global_level(logging.DEBUG)
    elif verbose:
        set_global_level(logging.INFO)
    else:
        set_global_level(logging.WARNING)

    # Create console UI
    ui = MigrationConsole(verbose=verbose)

    # Check for required tools
    tools = check_tools()
    if verbose:
        ui.print_tool_status(tools)

    # Note: We don't require Docker for image migration (using HTTP API)
    # Helm is only required for Helm chart migration

    # Build configuration
    try:
        source_creds = RegistryCredentials(
            registry=source_registry,
            username=source_user,
            password=source_password,
            insecure=source_insecure,
        )

        dest_creds = RegistryCredentials(
            registry=destination_registry,
            username=destination_user,
            password=destination_password,
            insecure=destination_insecure,
        )

        config = MigrationConfig(
            source=source_creds,
            destination=dest_creds,
            parallel_jobs=parallel,
            dry_run=dry_run,
            skip_existing=skip_existing,
            include_pattern=include,
            exclude_pattern=exclude,
        )

    except ValueError as e:
        ui.show_error("Configuration Error", str(e))
        raise typer.Exit(1) from e

    # Create and run migrator
    migrator = ContainerRegistryMigrator(config, ui)

    try:
        summary = asyncio.run(migrator.run())

        # Exit with appropriate code
        if summary.failed > 0:
            raise typer.Exit(1)

    except KeyboardInterrupt:
        ui.show_warning("\nMigration interrupted by user")
        raise typer.Exit(130) from None
    except Exception as e:
        logger.exception("Migration failed")
        ui.show_error("Unexpected Error", str(e))
        raise typer.Exit(1) from e


# Future migration commands can be added here
# Example:
# @migrate_app.command(name="database")
# def migrate_database(...):
#     """Migrate database data."""
#     pass


@app.command()
def info():
    """
    📋 Display system information and tool availability.
    """
    ui = MigrationConsole()

    console.print(Panel.fit(
        "[bold cyan] Migrator[/bold cyan] v1.0.0",
        border_style="cyan"
    ))
    console.print()

    # Check tools
    tools = check_tools()
    ui.print_tool_status(tools)

    # Show Python version
    console.print(f"\n[bold]Python:[/bold] {sys.version.split()[0]}")
    console.print(f"[bold]Platform:[/bold] {sys.platform}")


if __name__ == "__main__":
    app()
