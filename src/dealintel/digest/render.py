"""Digest rendering using Jinja2 templates."""

from collections import defaultdict
from datetime import datetime
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader

from dealintel.digest.select import DigestItem, select_digest_promos

logger = structlog.get_logger()


def group_by_store(promos: list[DigestItem]) -> dict[str, list[DigestItem]]:
    """Group promos by store name for organized display."""
    by_store = defaultdict(list)
    for item in promos:
        by_store[item["store_name"]].append(item)

    # Sort stores alphabetically
    return dict(sorted(by_store.items()))


def generate_digest(template_dir: str = "templates") -> tuple[str | None, int, int]:
    """Generate digest HTML from selected promos.

    Returns:
        tuple of (html_content, promo_count, store_count)
        Returns (None, 0, 0) if no promos to include.
    """
    promos = select_digest_promos()

    if not promos:
        logger.info("No promos to include in digest")
        return None, 0, 0

    # Group by store
    by_store = group_by_store(promos)

    # Load and render template
    template_path = Path(template_dir)
    if not template_path.exists():
        logger.error("Template directory not found", path=template_dir)
        raise FileNotFoundError(f"Template directory not found: {template_dir}")

    env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
    template = env.get_template("digest.html.j2")

    html = template.render(
        date=datetime.now().strftime("%B %d, %Y"),
        stores=by_store,
        promo_count=len(promos),
        store_count=len(by_store),
    )

    logger.info(
        "Generated digest",
        promo_count=len(promos),
        store_count=len(by_store),
    )

    return html, len(promos), len(by_store)
