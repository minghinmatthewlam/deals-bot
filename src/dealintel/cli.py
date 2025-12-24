"""CLI entry point using Typer."""

import structlog
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="dealintel",
    help="Deal Intelligence - Promotional email ingestion and digest generation.",
)
console = Console()

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)


@app.command()
def seed(stores_path: str = typer.Option("stores.yaml", help="Path to stores YAML file")) -> None:
    """Seed stores from stores.yaml."""
    from dealintel.seed import seed_stores

    console.print("[bold blue]Seeding stores...[/bold blue]")

    try:
        stats = seed_stores(stores_path)

        table = Table(title="Seed Results")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", style="green")
        table.add_row("Stores created", str(stats["stores_created"]))
        table.add_row("Stores updated", str(stats["stores_updated"]))
        table.add_row("Sources created", str(stats["sources_created"]))

        console.print(table)
        console.print("[bold green]Done![/bold green]")

    except FileNotFoundError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def gmail_auth() -> None:
    """Run Gmail OAuth flow."""
    from dealintel.gmail.auth import run_oauth_flow

    console.print("[bold blue]Starting Gmail OAuth flow...[/bold blue]")
    console.print("A browser window will open for authentication.")

    try:
        run_oauth_flow()
        console.print("[bold green]Gmail authentication successful![/bold green]")
    except FileNotFoundError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        console.print("\n[yellow]Tip:[/yellow] Download credentials.json from Google Cloud Console.")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def run(dry_run: bool = typer.Option(False, "--dry-run", help="Save preview HTML instead of sending email")) -> None:
    """Run daily pipeline."""
    from dealintel.jobs.daily import run_daily_pipeline

    if dry_run:
        console.print("[bold blue]Running in dry-run mode (no email will be sent)...[/bold blue]")
    else:
        console.print("[bold blue]Running daily pipeline...[/bold blue]")

    try:
        stats = run_daily_pipeline(dry_run=dry_run)

        if stats.get("error"):
            console.print(f"[bold yellow]Warning:[/bold yellow] {stats['error']}")

        # Display results
        table = Table(title="Pipeline Results")
        table.add_column("Phase", style="cyan")
        table.add_column("Metric", style="white")
        table.add_column("Value", style="green")

        # Ingest stats
        ingest = stats.get("ingest") or {}
        if ingest:
            metric_order = (
                "sources",
                "files",
                "fetched",
                "new",
                "matched",
                "unmatched",
                "skipped",
                "unchanged",
                "errors",
            )
            for source_name, source_stats in ingest.items():
                if not isinstance(source_stats, dict):
                    continue
                enabled = source_stats.get("enabled", True)
                table.add_row("Ingest", source_name, "enabled" if enabled else "disabled")
                if not enabled:
                    continue
                for metric in metric_order:
                    if metric in source_stats:
                        table.add_row("", f"  {metric}", str(source_stats[metric]))

        # Extract stats
        if stats.get("extract"):
            table.add_row("Extract", "Processed", str(stats["extract"].get("processed", 0)))
            table.add_row("", "Succeeded", str(stats["extract"].get("succeeded", 0)))
            table.add_row("", "Failed", str(stats["extract"].get("failed", 0)))

        # Merge stats
        if stats.get("merge"):
            table.add_row("Merge", "Created", str(stats["merge"].get("created", 0)))
            table.add_row("", "Updated", str(stats["merge"].get("updated", 0)))

        # Digest stats
        if stats.get("digest"):
            table.add_row("Digest", "Promos", str(stats["digest"].get("promo_count", 0)))
            table.add_row("", "Stores", str(stats["digest"].get("store_count", 0)))
            if dry_run and stats["digest"].get("preview_path"):
                table.add_row("", "Preview", stats["digest"]["preview_path"])
            elif stats["digest"].get("sent"):
                table.add_row("", "Sent", "Yes")

        console.print(table)

        if stats.get("success"):
            console.print("[bold green]Pipeline completed successfully![/bold green]")
        else:
            console.print("[bold yellow]Pipeline completed with warnings.[/bold yellow]")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show current status and recent runs."""
    from dealintel.db import get_db
    from dealintel.models import EmailRaw, Promo, Run, Store

    console.print("[bold blue]Deal Intelligence Status[/bold blue]\n")

    try:
        with get_db() as session:
            # Stores
            store_count = session.query(Store).filter_by(active=True).count()
            console.print(f"[cyan]Stores:[/cyan] {store_count} active")

            # Emails
            email_count = session.query(EmailRaw).count()
            pending_count = session.query(EmailRaw).filter_by(extraction_status="pending").count()
            console.print(f"[cyan]Emails:[/cyan] {email_count} total, {pending_count} pending extraction")

            # Promos
            promo_count = session.query(Promo).filter_by(status="active").count()
            console.print(f"[cyan]Promos:[/cyan] {promo_count} active")

            # Recent runs
            recent_runs = session.query(Run).order_by(Run.started_at.desc()).limit(5).all()
            if recent_runs:
                console.print("\n[bold]Recent Runs:[/bold]")
                table = Table()
                table.add_column("Date", style="cyan")
                table.add_column("Status", style="white")
                table.add_column("Sent", style="green")

                for run in recent_runs:
                    sent = "Yes" if run.digest_sent_at else "No"
                    table.add_row(run.digest_date_et, run.status, sent)

                console.print(table)

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        console.print("[yellow]Tip:[/yellow] Run 'make db-up && make migrate' to set up the database.")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
