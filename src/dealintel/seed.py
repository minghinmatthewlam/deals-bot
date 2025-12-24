"""Store seeding from stores.yaml."""

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from dealintel.db import get_db
from dealintel.models import Store, StoreSource


def seed_stores(stores_path: str = "stores.yaml") -> dict[str, int]:
    """Upsert stores and sources from YAML file.

    Returns:
        dict with counts: {stores_created, stores_updated, sources_created}
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
    sources_created = 0

    with get_db() as session:
        for store_data in stores_data:
            slug = store_data["slug"]
            existing = session.query(Store).filter_by(slug=slug).first()

            if existing:
                # Update existing store
                existing.name = store_data["name"]
                existing.website_url = store_data.get("website_url")
                existing.category = store_data.get("category")
                existing.active = store_data.get("active", True)
                store = existing
                stores_updated += 1
            else:
                # Create new store
                store = Store(
                    slug=slug,
                    name=store_data["name"],
                    website_url=store_data.get("website_url"),
                    category=store_data.get("category"),
                    active=store_data.get("active", True),
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
                    existing_source.priority = source_data.get("priority", 100)
                    existing_source.active = source_data.get("active", True)

    return {
        "stores_created": stores_created,
        "stores_updated": stores_updated,
        "sources_created": sources_created,
    }
