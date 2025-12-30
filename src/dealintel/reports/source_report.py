"""HTML report generator for source validation results."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from dealintel.config import settings


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


def render_source_report(
    *,
    attempts: list[dict],
    output_path: Path,
    store_filter: str | None = None,
    ignore_robots: bool | None = None,
) -> Path:
    if ignore_robots is None:
        ignore_robots = settings.ingest_ignore_robots

    env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
    template = env.get_template("source_report.html.j2")
    html = template.render(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        ignore_robots=ignore_robots,
        store_filter=store_filter,
        summary=_summarize_attempts(attempts),
        stores=_group_attempts_by_store(attempts),
    )
    output_path.write_text(html)
    return output_path
