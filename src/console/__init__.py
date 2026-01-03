"""
Rich console UI components for the Migrator.

Provides animated progress tracking, status displays, and beautiful CLI output.
"""
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from config import MigrationSummary


class ItemStatus(str, Enum):
    """Status of a migration item."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class MigrationItem:
    """Tracks the state of a single migration item."""
    name: str
    source: str
    destination: str
    status: ItemStatus = ItemStatus.PENDING
    error: str | None = None
    size_bytes: int = 0
    duration: float = 0.0


class MigrationConsole:
    """
    Rich-based console UI for migration operations.

    Provides:
    - Animated progress bars with spinners
    - Real-time status updates
    - Beautiful summary tables
    - Error highlighting
    """

    # Color scheme
    COLORS = {
        "primary": "#00d4aa",      # Cyan-green
        "secondary": "#7c3aed",    # Purple
        "success": "#22c55e",      # Green
        "error": "#ef4444",        # Red
        "warning": "#f59e0b",      # Amber
        "info": "#3b82f6",         # Blue
        "muted": "#6b7280",        # Gray
    }

    def __init__(self, verbose: bool = False):
        self.console = Console()
        self.verbose = verbose
        self._progress: Progress | None = None
        self._live: Live | None = None
        self._items: dict[str, MigrationItem] = {}
        self._start_time: float = 0.0

    def print_banner(self) -> None:
        """Display the application banner."""
        # Box width is 62 characters between the borders
        width = 62
        title = "MIGRATOR".center(width)
        subtitle = "Container Registry Migration Tool".center(width)
        
        banner = Text()
        banner.append("╔" + "═" * width + "╗\n", style="bold cyan")
        banner.append("║", style="bold cyan")
        banner.append(title, style="bold white")
        banner.append("║\n", style="bold cyan")
        banner.append("║", style="bold cyan")
        banner.append(subtitle, style="dim white")
        banner.append("║\n", style="bold cyan")
        banner.append("╚" + "═" * width + "╝", style="bold cyan")
        self.console.print(banner)
        self.console.print()

    def print_config_summary(   
        self,
        source_registry: str,
        dest_registry: str,
        parallel_jobs: int,
        dry_run: bool
    ) -> None:
        """Display configuration summary."""
        table = Table(
            show_header=False,
            box=box.ROUNDED,
            border_style="dim",
            padding=(0, 2),
        )
        table.add_column("Label", style="bold cyan")
        table.add_column("Value", style="white")

        table.add_row("📦 Source Registry", source_registry)
        table.add_row("🎯 Destination Registry", dest_registry)
        table.add_row("⚡ Parallel Jobs", str(parallel_jobs))
        table.add_row("🔄 Mode", "[yellow]DRY RUN[/yellow]" if dry_run else "[green]LIVE[/green]")

        panel = Panel(
            table,
            title="[bold]Migration Configuration[/bold]",
            border_style="blue",
        )
        self.console.print(panel)
        self.console.print()

    def print_tool_status(self, tools: dict[str, bool]) -> None:
        """Display tool availability status."""
        tree = Tree("🔧 [bold]Required Tools[/bold]")
        for tool, available in tools.items():
            if available:
                tree.add(f"[green]✓[/green] {tool}")
            else:
                tree.add(f"[red]✗[/red] {tool} [dim](not found)[/dim]")
        self.console.print(tree)
        self.console.print()

    @contextmanager
    def status(self, message: str) -> Generator[None, None, None]:
        """Show a spinner with status message."""
        with self.console.status(f"[bold cyan]{message}[/bold cyan]", spinner="dots"):
            yield

    def create_progress(self) -> Progress:
        """Create a configured progress bar."""
        return Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold blue]{task.description}[/bold blue]"),
            BarColumn(bar_width=40, style="cyan", complete_style="green"),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=self.console,
            expand=False,
        )

    @contextmanager
    def migration_progress(self, total_items: int) -> Generator[Progress, None, None]:
        """Context manager for migration progress tracking."""
        self._start_time = time.time()

        progress = self.create_progress()

        with Live(progress, console=self.console, refresh_per_second=10) as live:
            self._live = live
            self._progress = progress
            yield progress
            self._live = None
            self._progress = None

    def start_discovery(self) -> None:
        """Show discovery phase message."""
        self.console.print()
        self.console.print("[bold cyan]🔍 Discovering items to migrate...[/bold cyan]")

    def show_discovery_results(self, images: int, charts: int) -> None:
        """Show what was discovered."""
        self.console.print()
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Type")
        table.add_column("Count", justify="right")

        table.add_row("📦 Container Images", f"[bold]{images}[/bold]")
        table.add_row("📊 Helm Charts", f"[bold]{charts}[/bold]")
        table.add_row("─" * 20, "─" * 5)
        table.add_row("[bold]Total Items[/bold]", f"[bold cyan]{images + charts}[/bold cyan]")

        self.console.print(table)
        self.console.print()

    def show_item_success(self, item: str, size_mb: float | None = None) -> None:
        """Log successful migration of an item."""
        if self.verbose:
            size_str = f" ({size_mb:.1f} MB)" if size_mb else ""
            self.console.print(f"  [green]✓[/green] {item}{size_str}")

    def show_item_failure(self, item: str, error: str) -> None:
        """Log failed migration of an item."""
        self.console.print(f"  [red]✗[/red] {item}")
        if self.verbose:
            self.console.print(f"    [dim red]{error}[/dim red]")

    def show_item_skipped(self, item: str, reason: str = "already exists") -> None:
        """Log skipped item."""
        if self.verbose:
            self.console.print(f"  [yellow]⊘[/yellow] {item} [dim]({reason})[/dim]")

    def show_summary(self, summary: MigrationSummary) -> None:
        """Display final migration summary."""
        self.console.print()

        # Calculate stats
        duration = summary.total_duration_seconds
        size_mb = summary.total_size_bytes / (1024 * 1024)
        throughput = size_mb / duration if duration > 0 else 0

        # Status panel
        if summary.failed == 0:
            status_style = "green"
            status_icon = "✓"
            status_text = "MIGRATION COMPLETE"
        elif summary.successful > 0:
            status_style = "yellow"
            status_icon = "⚠"
            status_text = "MIGRATION PARTIAL"
        else:
            status_style = "red"
            status_icon = "✗"
            status_text = "MIGRATION FAILED"

        # Results table
        table = Table(
            show_header=False,
            box=box.ROUNDED,
            border_style=status_style,
            padding=(0, 2),
        )
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right")

        table.add_row("Total Items", str(summary.total_items))
        table.add_row("Successful", f"[green]{summary.successful}[/green]")
        table.add_row("Failed", f"[red]{summary.failed}[/red]" if summary.failed > 0 else "0")
        table.add_row("Skipped", f"[yellow]{summary.skipped}[/yellow]" if summary.skipped > 0 else "0")
        table.add_row("", "")
        table.add_row("Success Rate", f"{summary.success_rate:.1f}%")
        table.add_row("Duration", f"{duration:.1f}s")
        table.add_row("Data Transferred", f"{size_mb:.1f} MB")
        table.add_row("Throughput", f"{throughput:.1f} MB/s")

        panel = Panel(
            table,
            title=f"[bold {status_style}]{status_icon} {status_text}[/bold {status_style}]",
            border_style=status_style,
        )
        self.console.print(panel)

        # Show failed items if any
        if summary.failed > 0:
            self.console.print()
            self.console.print("[bold red]Failed Items:[/bold red]")
            for result in summary.results:
                if not result.success and not result.skipped:
                    self.console.print(f"  [red]•[/red] {result.source}")
                    if result.error:
                        self.console.print(f"    [dim]{result.error}[/dim]")

    def show_dry_run_notice(self) -> None:
        """Display dry run notice."""
        self.console.print()
        panel = Panel(
            "[yellow]No changes were made. Remove --dry-run to perform actual migration.[/yellow]",
            title="[bold yellow]DRY RUN MODE[/bold yellow]",
            border_style="yellow",
        )
        self.console.print(panel)

    def show_error(self, title: str, message: str) -> None:
        """Display an error panel."""
        panel = Panel(
            f"[red]{message}[/red]",
            title=f"[bold red]❌ {title}[/bold red]",
            border_style="red",
        )
        self.console.print(panel)

    def show_warning(self, message: str) -> None:
        """Display a warning message."""
        self.console.print(f"[yellow]⚠ {message}[/yellow]")

    def show_info(self, message: str) -> None:
        """Display an info message."""
        self.console.print(f"[cyan]ℹ {message}[/cyan]")

    def confirm(self, message: str, default: bool = False) -> bool:
        """Ask for user confirmation."""
        from rich.prompt import Confirm
        return Confirm.ask(message, default=default, console=self.console)
