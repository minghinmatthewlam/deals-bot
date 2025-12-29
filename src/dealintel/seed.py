"""Store seeding from stores.yaml."""

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from dealintel.db import get_db
from dealintel.models import Store, StoreSource


def seed_stores(stores_path: str = "stores.yaml") -> dict[str, int]:
    """Upsert stores and sources from YAML file.

    Returns:
        dict with counts: {stores_created, stores_updated, stores_unchanged, sources_created, sources_updated}
    """
    path = Path(stores_path)
    if not path.exists():
        raise FileNotFoundError(f"Stores file not found: {stores_path}")

    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("stores.yaml must contain a top-level mapping")
    stores_data: list[dict[str, Any]] = data.get("stores", [])

    stores_created = 0
    stores_updated = 0
    stores_unchanged = 0
    sources_created = 0
    sources_updated = 0

    with get_db() as session:
        for store_data in stores_data:
            slug = store_data["slug"]
            existing = session.query(Store).filter_by(slug=slug).first()

            if existing:
                # Update existing store
                updated = False
                fields = {
                    "name": store_data["name"],
                    "website_url": store_data.get("website_url"),
                    "tos_url": store_data.get("tos_url"),
                    "category": store_data.get("category"),
                    "active": store_data.get("active", True),
                    "robots_policy": store_data.get("robots_policy"),
                    "crawl_delay_seconds": store_data.get("crawl_delay_seconds"),
                    "max_requests_per_run": store_data.get("max_requests_per_run"),
                    "requires_login": store_data.get("requires_login", False),
                    "allow_login": store_data.get("allow_login", False),
                    "notes": store_data.get("notes"),
                }

                for field_name, value in fields.items():
                    if getattr(existing, field_name) != value:
                        setattr(existing, field_name, value)
                        updated = True

                store = existing
                if updated:
                    stores_updated += 1
                else:
                    stores_unchanged += 1
            else:
                # Create new store
                store = Store(
                    slug=slug,
                    name=store_data["name"],
                    website_url=store_data.get("website_url"),
                    tos_url=store_data.get("tos_url"),
                    category=store_data.get("category"),
                    active=store_data.get("active", True),
                    robots_policy=store_data.get("robots_policy"),
                    crawl_delay_seconds=store_data.get("crawl_delay_seconds"),
                    max_requests_per_run=store_data.get("max_requests_per_run"),
                    requires_login=store_data.get("requires_login", False),
                    allow_login=store_data.get("allow_login", False),
                    notes=store_data.get("notes"),
                )
                session.add(store)
                session.flush()  # Get the ID
                stores_created += 1

            # Upsert sources
            for source_data in store_data.get("sources", []):
                existing_source = (
                    session.query(StoreSource)
                    .filter_by(
                        store_id=store.id,
                        source_type=source_data["type"],
                        pattern=source_data["pattern"],
                    )
                    .first()
                )

                if not existing_source:
                    source = StoreSource(
                        store_id=store.id,
                        source_type=source_data["type"],
                        pattern=source_data["pattern"],
                        priority=source_data.get("priority", 100),
                        active=source_data.get("active", True),
                    )
                    session.add(source)
                    sources_created += 1
                else:
                    # Update priority if changed
                    priority = source_data.get("priority", 100)
                    active = source_data.get("active", True)
                    if existing_source.priority != priority or existing_source.active != active:
                        existing_source.priority = priority
                        existing_source.active = active
                        sources_updated += 1

    return {
        "stores_created": stores_created,
        "stores_updated": stores_updated,
        "stores_unchanged": stores_unchanged,
        "sources_created": sources_created,
        "sources_updated": sources_updated,
    }
