"""Store seeding from stores.yaml."""

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from dealintel.db import get_db
import json

from dealintel.models import SourceConfig, Store, StoreSource

SOURCE_TYPE_ALIASES = {
    "web_url": "category",
    "category_page": "category",
    "browser_page": "browser",
    "json_endpoint": "json",
    "api": "json",
}

SOURCE_TIER_DEFAULTS = {
    "sitemap": 1,
    "rss": 1,
    "json": 2,
    "category": 3,
    "browser": 4,
    "newsletter": 4,
}


def _normalize_source_type(source_type: str) -> str:
    return SOURCE_TYPE_ALIASES.get(source_type, source_type)


def _source_tier(source_type: str, override: int | None = None) -> int:
    if override is not None:
        return override
    return SOURCE_TIER_DEFAULTS.get(source_type, 3)


def _source_config_key(config: dict[str, Any]) -> str:
    for key in ("url", "endpoint", "sitemap_url", "feed_url", "signup_url"):
        value = config.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(config, sort_keys=True)


def seed_stores(stores_path: str = "stores.yaml") -> dict[str, int]:
    """Upsert stores and sources from YAML file.

    Returns:
        dict with counts:
            {stores_created, stores_updated, stores_unchanged,
             sources_created, sources_updated,
             source_configs_created, source_configs_updated}
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
    source_configs_created = 0
    source_configs_updated = 0

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

            # Upsert sources (email matching) + source configs (web/adapters)
            for source_data in store_data.get("sources", []):
                source_type = source_data["type"]
                normalized_type = _normalize_source_type(source_type)

                if source_type == "web_url":
                    url = source_data.get("pattern") or source_data.get("url", "")
                    if isinstance(url, str):
                        lowered = url.lower()
                        if "feed" in lowered or "rss" in lowered:
                            normalized_type = "rss"
                        elif lowered.endswith(".xml"):
                            normalized_type = "sitemap"

                if source_type.startswith("gmail_"):
                    existing_source = (
                        session.query(StoreSource)
                        .filter_by(
                            store_id=store.id,
                            source_type=source_type,
                            pattern=source_data["pattern"],
                        )
                        .first()
                    )

                    if not existing_source:
                        source = StoreSource(
                            store_id=store.id,
                            source_type=source_type,
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
                    continue

                config = {key: value for key, value in source_data.items() if key not in {"type", "priority"}}
                if "pattern" in config and "url" not in config:
                    config["url"] = config.pop("pattern")
                config_key = _source_config_key(config)
                tier = _source_tier(normalized_type, source_data.get("tier"))

                existing_config = (
                    session.query(SourceConfig)
                    .filter_by(store_id=store.id, source_type=normalized_type, config_key=config_key)
                    .first()
                )

                if not existing_config:
                    session.add(
                        SourceConfig(
                            store_id=store.id,
                            source_type=normalized_type,
                            tier=tier,
                            config_key=config_key,
                            config_json=config,
                            active=source_data.get("active", True),
                        )
                    )
                    source_configs_created += 1
                else:
                    updated = False
                    if existing_config.config_json != config:
                        existing_config.config_json = config
                        updated = True
                    if existing_config.tier != tier:
                        existing_config.tier = tier
                        updated = True
                    active = source_data.get("active", True)
                    if existing_config.active != active:
                        existing_config.active = active
                        updated = True
                    if updated:
                        source_configs_updated += 1

    return {
        "stores_created": stores_created,
        "stores_updated": stores_updated,
        "stores_unchanged": stores_unchanged,
        "sources_created": sources_created,
        "sources_updated": sources_updated,
        "source_configs_created": source_configs_created,
        "source_configs_updated": source_configs_updated,
    }
