"""CLI entry point using Typer."""

import structlog
import typer
from rich.console import Console
from rich.table import Table
from pathlib import Path

import yaml  # type: ignore[import-untyped]

app = typer.Typer(
    name="dealintel",
    help="Deal Intelligence - Promotional email ingestion and digest generation.",
)
console = Console()
stores_app = typer.Typer(help="Store discovery and allowlist helpers.")
app.add_typer(stores_app, name="stores")
sources_app = typer.Typer(help="Source validation helpers.")
app.add_typer(sources_app, name="sources")

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


def _load_store_catalog(stores_path: str) -> list[dict]:
    path = Path(stores_path)
    if not path.exists():
        raise FileNotFoundError(f"Stores file not found: {stores_path}")
    data = yaml.safe_load(path.read_text()) or {}
    stores = data.get("stores", [])
    if not isinstance(stores, list):
        raise ValueError("stores.yaml must contain a list under 'stores'")
    return stores


def _parse_store_selection(selection: str, stores: list[dict]) -> list[str]:
    tokens = [token.strip() for token in selection.split(",") if token.strip()]
    if not tokens:
        return []
    if len(tokens) == 1 and tokens[0].lower() == "all":
        return [store.get("slug", "").strip().lower() for store in stores if store.get("slug")]

    slug_map = {str(i + 1): store.get("slug") for i, store in enumerate(stores)}
    slugs: list[str] = []
    for token in tokens:
        if token in slug_map and slug_map[token]:
            slugs.append(slug_map[token])
        else:
            slugs.append(token)
    return slugs


def _summarize_attempts(attempts: list[dict]) -> dict:
    summary = {"total": 0, "success": 0, "empty": 0, "failure": 0, "error": 0}
    summary["total"] = len(attempts)
    for attempt in attempts:
        status = attempt.get("status")
        if status in summary:
            summary[status] += 1
        else:
            summary["error"] += 1
    return summary


def _group_attempts_by_store(attempts: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for attempt in attempts:
        slug = attempt.get("store")
        if not slug:
            continue
        entry = grouped.setdefault(
            slug,
            {"slug": slug, "name": attempt.get("store_name") or slug, "attempts": []},
        )
        entry["attempts"].append(attempt)
    return sorted(grouped.values(), key=lambda item: item["name"].lower())


def _render_source_report(
    *,
    attempts: list[dict],
    output_path: Path,
    store_filter: str | None,
) -> None:
    from datetime import datetime
    from jinja2 import Environment, FileSystemLoader
    from dealintel.config import settings

    env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
    template = env.get_template("source_report.html.j2")
    html = template.render(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        ignore_robots=settings.ingest_ignore_robots,
        store_filter=store_filter,
        summary=_summarize_attempts(attempts),
        stores=_group_attempts_by_store(attempts),
    )
    output_path.write_text(html)


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
        table.add_row("Stores created", str(stats.get("stores_created", 0)))
        table.add_row("Stores updated", str(stats.get("stores_updated", 0)))
        table.add_row("Stores unchanged", str(stats.get("stores_unchanged", 0)))
        table.add_row("Sources created", str(stats.get("sources_created", 0)))
        table.add_row("Sources updated", str(stats.get("sources_updated", 0)))
        table.add_row("Source configs created", str(stats.get("source_configs_created", 0)))
        table.add_row("Source configs updated", str(stats.get("source_configs_updated", 0)))

        console.print(table)
        console.print("[bold green]Done![/bold green]")

    except FileNotFoundError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def init(
    stores_path: str = typer.Option("stores.yaml", help="Path to stores YAML file"),
    prefs_path: str = typer.Option("preferences.yaml", help="Path to preferences file"),
) -> None:
    """Interactive onboarding for store selection and first run."""
    from dealintel.prefs import load_preferences, set_store_allowlist

    console.print("[bold blue]DealIntel Setup[/bold blue]")
    try:
        stores = _load_store_catalog(stores_path)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)

    if not stores:
        console.print("[yellow]No stores found in stores.yaml.[/yellow]")
        raise typer.Exit(1)

    table = Table(title="Available Stores")
    table.add_column("#", style="cyan")
    table.add_column("Slug", style="white")
    table.add_column("Name", style="green")
    table.add_column("Category", style="magenta")
    for idx, store in enumerate(stores, start=1):
        table.add_row(
            str(idx),
            store.get("slug", ""),
            store.get("name", ""),
            store.get("category", "") or "",
        )
    console.print(table)

    selection = typer.prompt(
        "Enter store slugs or numbers (comma-separated), or 'all'",
        default="",
    )
    selected = _parse_store_selection(selection, stores)
    if not selected:
        console.print("[yellow]No stores selected; keeping existing allowlist.[/yellow]")
    else:
        normalized = set_store_allowlist(selected, prefs_path)
        console.print(f"[green]Allowlist saved:[/green] {', '.join(normalized)}")

    prefs = load_preferences(prefs_path)
    if not prefs.stores.allowlist:
        console.print("[yellow]Allowlist is empty; all stores will run.[/yellow]")

    if typer.confirm("Run a dry-run now?", default=False):
        from dealintel.jobs.daily import run_daily_pipeline

        stats = run_daily_pipeline(dry_run=True)
        if stats.get("digest", {}).get("preview_path"):
            console.print(f"[green]Digest preview saved:[/green] {stats['digest']['preview_path']}")


@app.command()
def sync_stores(stores_path: str = typer.Option("stores.yaml", help="Path to stores YAML file")) -> None:
    """Sync stores from stores.yaml (YAML is source of truth)."""
    seed(stores_path)


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


@sources_app.command("validate")
def validate_sources(
    store: str | None = typer.Option(None, "--store", help="Limit to a store slug"),
) -> None:
    """Validate source configurations with lightweight health checks."""
    from dealintel.db import get_db
    from dealintel.models import SourceConfig, Store
    from dealintel.web.rate_limit import RateLimiter
    from dealintel.web.tiered import build_adapter

    with get_db() as session:
        query = session.query(SourceConfig).join(Store).filter(SourceConfig.active == True)  # noqa: E712
        if store:
            query = query.filter(Store.slug == store)
        configs = query.all()

        table = Table(title="Source Validation")
        table.add_column("Store", style="cyan")
        table.add_column("Source", style="magenta")
        table.add_column("Status", style="green")
        table.add_column("Message", style="white")

        rate_limiter = RateLimiter()
        for cfg in configs:
            store_row = session.query(Store).filter_by(id=cfg.store_id).first()
            if not store_row:
                continue
            adapter = build_adapter(store_row, cfg, rate_limiter)
            if not adapter:
                continue
            status = adapter.health_check()
            table.add_row(
                store_row.slug,
                f"{cfg.source_type}",
                "ok" if status.ok else "fail",
                status.message,
            )

        console.print(table)


@sources_app.command("debug")
def debug_source(
    store: str = typer.Argument(..., help="Store slug"),
    source_type: str | None = typer.Option(None, "--source-type", help="Filter by source type"),
    config_key: str | None = typer.Option(None, "--config-key", help="Filter by config key"),
) -> None:
    """Run a single source adapter and print its result."""
    from dealintel.db import get_db
    from dealintel.models import SourceConfig, Store
    from dealintel.web.rate_limit import RateLimiter
    from dealintel.web.tiered import build_adapter

    with get_db() as session:
        store_row = session.query(Store).filter_by(slug=store).first()
        if not store_row:
            console.print(f"[red]Store not found:[/red] {store}")
            raise typer.Exit(1)

        configs = [cfg for cfg in store_row.source_configs if cfg.active]
        if source_type:
            configs = [cfg for cfg in configs if cfg.source_type == source_type]
        if config_key:
            configs = [cfg for cfg in configs if cfg.config_key == config_key]

        if not configs:
            console.print("[yellow]No matching source configs found.[/yellow]")
            raise typer.Exit(1)
        if len(configs) > 1:
            table = Table(title="Matching Sources")
            table.add_column("Source", style="magenta")
            table.add_column("Config Key", style="white")
            for cfg in configs:
                table.add_row(cfg.source_type, cfg.config_key)
            console.print(table)
            console.print("[yellow]Please specify --source-type and/or --config-key.[/yellow]")
            raise typer.Exit(1)

        cfg = configs[0]
        adapter = build_adapter(store_row, cfg, RateLimiter())
        if not adapter:
            console.print("[red]Unable to build adapter.[/red]")
            raise typer.Exit(1)

        result = adapter.discover()
        table = Table(title="Source Debug Result")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Store", store_row.slug)
        table.add_row("Source", cfg.source_type)
        table.add_row("Status", result.status.value)
        table.add_row("Error Code", result.error_code or "")
        table.add_row("Message", result.message or "")
        table.add_row("Signals", str(len(result.signals)))
        table.add_row("HTTP Requests", str(result.http_requests))
        table.add_row("Bytes Read", str(result.bytes_read))
        table.add_row("Duration (ms)", str(result.duration_ms or ""))

        console.print(table)

        if result.sample_urls:
            urls = Table(title="Sample URLs")
            urls.add_column("URL", style="white")
            for url in result.sample_urls:
                urls.add_row(url)
            console.print(urls)


@sources_app.command("report")
def report_sources(
    store: str | None = typer.Option(None, "--store", help="Limit to a store slug"),
    output: str = typer.Option("source_report.html", "--output", help="Output HTML report path"),
) -> None:
    """Generate an HTML report of source discovery results."""
    from dealintel.db import get_db
    from dealintel.models import SourceConfig, Store
    from dealintel.prefs import get_store_allowlist
    from dealintel.web.rate_limit import RateLimiter
    from dealintel.web.tiered import build_adapter

    console.print("[bold blue]Generating source report...[/bold blue]")

    attempts: list[dict] = []
    allowlist = get_store_allowlist()
    with get_db() as session:
        query = (
            session.query(SourceConfig, Store)
            .join(Store)
            .filter(SourceConfig.active == True)  # noqa: E712
        )
        if store:
            query = query.filter(Store.slug == store)
        if allowlist:
            query = query.filter(Store.slug.in_(allowlist))
        rows = query.all()

        rate_limiter = RateLimiter()
        for cfg, store_row in rows:
            adapter = build_adapter(store_row, cfg, rate_limiter)
            if not adapter:
                continue
            try:
                result = adapter.discover()
                attempts.append(
                    {
                        "store": store_row.slug,
                        "store_name": store_row.name,
                        "tier": adapter.tier.value,
                        "source_type": cfg.source_type,
                        "config_key": cfg.config_key,
                        "status": result.status.value,
                        "message": result.message,
                        "error_code": result.error_code,
                        "signals": len(result.signals),
                        "http_requests": result.http_requests,
                        "bytes_read": result.bytes_read,
                        "duration_ms": result.duration_ms,
                        "sample_urls": result.sample_urls,
                    }
                )
            except Exception as exc:
                attempts.append(
                    {
                        "store": store_row.slug,
                        "store_name": store_row.name,
                        "tier": adapter.tier.value,
                        "source_type": cfg.source_type,
                        "config_key": cfg.config_key,
                        "status": "error",
                        "message": str(exc),
                        "error_code": "exception",
                        "signals": 0,
                        "http_requests": 0,
                        "bytes_read": 0,
                        "duration_ms": None,
                        "sample_urls": [],
                    }
                )

    output_path = Path(output)
    _render_source_report(attempts=attempts, output_path=output_path, store_filter=store)
    console.print(f"[green]Report saved:[/green] {output_path}")


@stores_app.command("list")
def list_stores(stores_path: str = typer.Option("stores.yaml", help="Path to stores YAML file")) -> None:
    """List available stores."""
    stores = _load_store_catalog(stores_path)
    table = Table(title="Stores")
    table.add_column("Slug", style="white")
    table.add_column("Name", style="green")
    table.add_column("Category", style="magenta")
    for store in stores:
        table.add_row(store.get("slug", ""), store.get("name", ""), store.get("category", "") or "")
    console.print(table)


@stores_app.command("search")
def search_stores(
    query: str = typer.Argument(..., help="Search term"),
    stores_path: str = typer.Option("stores.yaml", help="Path to stores YAML file"),
) -> None:
    """Search stores by slug or name."""
    stores = _load_store_catalog(stores_path)
    query_lower = query.lower()
    matches = [
        store
        for store in stores
        if query_lower in (store.get("slug", "").lower())
        or query_lower in (store.get("name", "").lower())
    ]
    table = Table(title=f"Stores matching '{query}'")
    table.add_column("Slug", style="white")
    table.add_column("Name", style="green")
    table.add_column("Category", style="magenta")
    for store in matches:
        table.add_row(store.get("slug", ""), store.get("name", ""), store.get("category", "") or "")
    console.print(table)


@stores_app.command("allowlist")
def manage_allowlist(
    set_: list[str] = typer.Option(None, "--set", help="Replace the allowlist"),
    add: list[str] = typer.Option(None, "--add", help="Add stores to the allowlist"),
    remove: list[str] = typer.Option(None, "--remove", help="Remove stores from the allowlist"),
    prefs_path: str = typer.Option("preferences.yaml", help="Path to preferences file"),
) -> None:
    """Show or update the store allowlist."""
    from dealintel.prefs import load_preferences, normalize_store_slugs, save_preferences

    prefs = load_preferences(prefs_path)
    current = set(normalize_store_slugs(prefs.stores.allowlist))

    if set_:
        updated = set(normalize_store_slugs(set_))
    else:
        updated = set(current)
        if add:
            updated.update(normalize_store_slugs(add))
        if remove:
            updated.difference_update(normalize_store_slugs(remove))

    if set_ or add or remove:
        prefs.stores.allowlist = sorted(updated)
        save_preferences(prefs, prefs_path)
        console.print(f"[green]Allowlist updated:[/green] {', '.join(prefs.stores.allowlist)}")
    else:
        console.print(f"[cyan]Current allowlist:[/cyan] {', '.join(sorted(current)) or '(none)'}")


@app.command()
def inbound_import(eml_dir: str = typer.Option("inbound_eml", "--dir", "-d")) -> None:
    """Import emails from .eml files."""
    from dealintel.inbound.ingest import ingest_inbound_eml_dir

    stats = ingest_inbound_eml_dir(eml_dir)
    console.print(stats)


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
            table.add_row("", "Skipped duplicates", str(stats["extract"].get("skipped_duplicates", 0)))

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

        digest_items = stats.get("digest", {}).get("items") or []
        if digest_items:
            items_table = Table(title="Deals Found")
            items_table.add_column("Store", style="cyan")
            items_table.add_column("Method", style="magenta")
            items_table.add_column("Badge", style="green")
            items_table.add_column("Headline", style="white")
            for item in digest_items:
                items_table.add_row(
                    item.get("store", ""),
                    item.get("source_type", ""),
                    item.get("badge", ""),
                    item.get("headline", ""),
                )
            console.print(items_table)

        attempts = stats.get("ingest", {}).get("web", {}).get("attempts") or []
        if attempts:
            failures = [item for item in attempts if item.get("status") != "success"]
            if failures:
                fail_table = Table(title="Source Attempts (Non-Success)")
                fail_table.add_column("Store", style="cyan")
                fail_table.add_column("Method", style="magenta")
                fail_table.add_column("Status", style="yellow")
                fail_table.add_column("Reason", style="red")
                for item in failures:
                    fail_table.add_row(
                        str(item.get("store", "")),
                        str(item.get("source_type", "")),
                        str(item.get("status", "")),
                        str(item.get("message") or item.get("error_code") or ""),
                    )
                console.print(fail_table)

        if stats.get("success"):
            console.print("[bold green]Pipeline completed successfully![/bold green]")
        else:
            console.print("[bold yellow]Pipeline completed with warnings.[/bold yellow]")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def weekly(dry_run: bool = typer.Option(False, "--dry-run", help="Save preview HTML instead of sending email")) -> None:
    """Run weekly pipeline (newsletter + tiered web ingest)."""
    from dealintel.jobs.weekly import run_weekly_pipeline

    if dry_run:
        console.print("[bold blue]Running weekly pipeline in dry-run mode...[/bold blue]")
    else:
        console.print("[bold blue]Running weekly pipeline...[/bold blue]")

    try:
        stats = run_weekly_pipeline(dry_run=dry_run)

        if stats.get("error"):
            console.print(f"[bold yellow]Warning:[/bold yellow] {stats['error']}")

        table = Table(title="Weekly Pipeline Results")
        table.add_column("Phase", style="cyan")
        table.add_column("Metric", style="white")
        table.add_column("Value", style="green")

        if stats.get("newsletter"):
            table.add_row("Newsletter", "Attempted", str(stats["newsletter"].get("attempted", 0)))
            table.add_row("", "Submitted", str(stats["newsletter"].get("submitted", 0)))
            table.add_row("", "Confirmed", str(stats["newsletter"].get("confirmed", 0)))
            table.add_row("", "Failed", str(stats["newsletter"].get("failed", 0)))

        if stats.get("confirmations"):
            table.add_row("Confirmations", "Matched", str(stats["confirmations"].get("matched", 0)))
            table.add_row("", "Stored", str(stats["confirmations"].get("stored", 0)))

        ingest = stats.get("ingest") or {}
        if ingest:
            web_stats = ingest.get("web") if isinstance(ingest.get("web"), dict) else ingest
            table.add_row("Ingest", "Sources", str(web_stats.get("sources", 0)))
            table.add_row("", "Signals", str(web_stats.get("signals", 0)))
            table.add_row("", "New", str(web_stats.get("new", 0)))
            table.add_row("", "Errors", str(web_stats.get("errors", 0)))

        if stats.get("extract"):
            table.add_row("Extract", "Processed", str(stats["extract"].get("processed", 0)))
            table.add_row("", "Succeeded", str(stats["extract"].get("succeeded", 0)))
            table.add_row("", "Failed", str(stats["extract"].get("failed", 0)))

        if stats.get("merge"):
            table.add_row("Merge", "Created", str(stats["merge"].get("created", 0)))
            table.add_row("", "Updated", str(stats["merge"].get("updated", 0)))

        if stats.get("digest"):
            table.add_row("Digest", "Promos", str(stats["digest"].get("promo_count", 0)))
            table.add_row("", "Stores", str(stats["digest"].get("store_count", 0)))
            if dry_run and stats["digest"].get("preview_path"):
                table.add_row("", "Preview", stats["digest"]["preview_path"])
            elif stats["digest"].get("sent"):
                table.add_row("", "Sent", "Yes")

        console.print(table)

        digest_items = stats.get("digest", {}).get("items") or []
        if digest_items:
            items_table = Table(title="Deals Found")
            items_table.add_column("Store", style="cyan")
            items_table.add_column("Method", style="magenta")
            items_table.add_column("Badge", style="green")
            items_table.add_column("Headline", style="white")
            for item in digest_items:
                items_table.add_row(
                    item.get("store", ""),
                    item.get("source_type", ""),
                    item.get("badge", ""),
                    item.get("headline", ""),
                )
            console.print(items_table)

        attempts = stats.get("ingest", {}).get("web", {}).get("attempts") or []
        if attempts:
            failures = [item for item in attempts if item.get("status") != "success"]
            if failures:
                fail_table = Table(title="Source Attempts (Non-Success)")
                fail_table.add_column("Store", style="cyan")
                fail_table.add_column("Method", style="magenta")
                fail_table.add_column("Status", style="yellow")
                fail_table.add_column("Reason", style="red")
                for item in failures:
                    fail_table.add_row(
                        str(item.get("store", "")),
                        str(item.get("source_type", "")),
                        str(item.get("status", "")),
                        str(item.get("message") or item.get("error_code") or ""),
                    )
                console.print(fail_table)

        if stats.get("success"):
            console.print("[bold green]Weekly pipeline completed successfully![/bold green]")
        else:
            console.print("[bold yellow]Weekly pipeline completed with warnings.[/bold yellow]")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def newsletter_subscribe() -> None:
    """Run newsletter subscription agent."""
    from dealintel.newsletter.agent import NewsletterAgent

    console.print("[bold blue]Running newsletter subscription agent...[/bold blue]")

    try:
        agent = NewsletterAgent()
        stats = agent.subscribe_all()

        table = Table(title="Newsletter Subscription Results")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        for key in ("attempted", "submitted", "confirmed", "failed"):
            table.add_row(key.replace("_", " ").title(), str(stats.get(key, 0)))

        console.print(table)
        console.print("[bold green]Newsletter subscription run completed![/bold green]")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def confirmations(
    days: int = typer.Option(7, help="Days to look back if history cursor is missing"),
    click_links: bool = typer.Option(True, help="Click pending confirmation links"),
) -> None:
    """Poll for newsletter confirmation emails."""
    from dealintel.jobs.confirmations import run_confirmation_poll

    console.print("[bold blue]Polling confirmation emails...[/bold blue]")

    try:
        stats = run_confirmation_poll(days=days, click_links=click_links)

        if stats.get("error"):
            console.print(f"[bold yellow]Warning:[/bold yellow] {stats['error']}")

        table = Table(title="Confirmation Poll Results")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        for key in ("scanned", "matched", "stored", "skipped_existing", "missing_link"):
            if key in stats:
                table.add_row(key.replace("_", " ").title(), str(stats[key]))

        for key in ("click_checked", "click_clicked", "click_needs_human", "click_errors"):
            if key in stats:
                table.add_row(key.replace("_", " ").title(), str(stats[key]))

        console.print(table)

        if stats.get("success"):
            console.print("[bold green]Confirmation poll completed successfully![/bold green]")
        else:
            console.print("[bold yellow]Confirmation poll completed with warnings.[/bold yellow]")

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
