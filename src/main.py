"""
Migrator - CLI Entry Point

A modular CLI application for migrating data between systems.
Supports container registry migration with extensible architecture for future migrations.
"""
import asyncio
import logging
import sys

import typer
from rich.console import Console
from rich.panel import Panel

from config import MigrationConfig, RegistryCredentials
from console import MigrationConsole
from migrators.container_registry import ContainerRegistryMigrator
from utilities.logger import get_logger, set_global_level
from utilities.version import version

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
            f"[bold cyan]Migrator[/bold cyan] {version}\n"
            "[dim]Container Registry Migration Tool[/dim]",
            border_style="cyan"
        ))
        raise typer.Exit()





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
        help="Source registry URL (e.g., source.registry.io or source.registry.io/project)",
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
    source_namespace: str | None = typer.Option(
        None,
        "--source-namespace",
        "-sn",
        help="Source namespace/project prefix (overrides path in registry URL)",
        rich_help_panel="Source Registry",
    ),

    # Destination registry options
    destination_registry: str = typer.Option(
        ...,
        "--destination-registry",
        "-dr",
        help="Destination registry URL (e.g., dest.registry.io or dest.registry.io/project)",
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
    destination_namespace: str | None = typer.Option(
        None,
        "--destination-namespace",
        "-dn",
        help="Destination namespace/project prefix (overrides path in registry URL)",
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
    skip_charts: bool = typer.Option(
        False,
        "--skip-charts",
        help="Skip Helm chart migration (only migrate container images)",
        rich_help_panel="Filtering",
    ),
    skip_images: bool = typer.Option(
        False,
        "--skip-images",
        help="Skip container image migration (only migrate Helm charts)",
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
    log_file: str | None = typer.Option(
        None,
        "--log-file",
        "-l",
        help="Write logs to file (keeps console clean for progress)",
        rich_help_panel="Output",
    ),
    # Resume and performance options
    resume: bool = typer.Option(
        True,
        "--resume/--no-resume",
        help="Resume previous incomplete migration if found",
        rich_help_panel="Performance",
    ),
    state_dir: str = typer.Option(
        ".migrator-state",
        "--state-dir",
        help="Directory for state files (for resume capability)",
        rich_help_panel="Performance",
    ),
    layer_concurrency: int = typer.Option(
        3,
        "--layer-concurrency",
        "-lc",
        min=1,
        max=10,
        help="Number of parallel layer transfers per image",
        rich_help_panel="Performance",
    ),
    scan_concurrency: int = typer.Option(
        10,
        "--scan-concurrency",
        "-sc",
        min=1,
        max=50,
        help="Number of parallel repository scans (reduce if getting disconnections)",
        rich_help_panel="Performance",
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
    • Resume capability with checkpoint saving

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
    # Set up file logging first (if specified)
    from utilities.logger import set_log_file
    if log_file:
        set_log_file(log_file)
        console.print(f"[dim]Logging to: {log_file}[/dim]")
    
    if debug:
        set_global_level(logging.DEBUG)
    elif verbose:
        set_global_level(logging.INFO)
    else:
        set_global_level(logging.WARNING)

    # Create console UI
    ui = MigrationConsole(verbose=verbose)



    # Note: We don't require Docker for image migration (using HTTP API)
    # Helm is only required for Helm chart migration

    # Build configuration
    try:
        source_creds = RegistryCredentials(
            registry=source_registry,
            username=source_user,
            password=source_password,
            insecure=source_insecure,
            namespace=source_namespace,
        )

        dest_creds = RegistryCredentials(
            registry=destination_registry,
            username=destination_user,
            password=destination_password,
            insecure=destination_insecure,
            namespace=destination_namespace,
        )

        config = MigrationConfig(
            source=source_creds,
            destination=dest_creds,
            parallel_jobs=parallel,
            dry_run=dry_run,
            skip_existing=skip_existing,
            include_pattern=include,
            exclude_pattern=exclude,
            skip_charts=skip_charts,
            skip_images=skip_images,
            resume=resume,
            state_dir=state_dir,
            layer_concurrency=layer_concurrency,
            scan_concurrency=scan_concurrency,
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

    except (KeyboardInterrupt, asyncio.CancelledError):
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
    📋 Display system information.
    """
    ui = MigrationConsole()

    console.print(Panel.fit(
        f"[bold cyan] Migrator[/bold cyan] {version}",
        border_style="cyan"
    ))
    console.print()

    # Show Python version
    console.print(f"\n[bold]Python:[/bold] {sys.version.split()[0]}")
    console.print(f"[bold]Platform:[/bold] {sys.platform}")


if __name__ == "__main__":
    app()
