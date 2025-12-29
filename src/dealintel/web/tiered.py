"""Tiered web ingestion pipeline."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from dealintel.db import get_db
from dealintel.gmail.parse import compute_body_hash
from dealintel.ingest.signals import RawSignal
from dealintel.models import EmailRaw, SourceConfig, Store, StoreSource
from dealintel.storage.payloads import ensure_blob_record, prepare_payload
from dealintel.web.adapters.base import AdapterError, SourceTier
from dealintel.web.adapters.browser import BrowserAdapter
from dealintel.web.adapters.category import CategoryPageAdapter
from dealintel.web.adapters.json_endpoint import JsonEndpointAdapter
from dealintel.web.adapters.rss import RssAdapter
from dealintel.web.adapters.sitemap import SitemapAdapter
from dealintel.web.rate_limit import RateLimiter

logger = structlog.get_logger()

LEGACY_WEB_SOURCE_TYPES = {"web_url"}


def ingest_tiered_sources() -> dict[str, int | bool]:
    stats: dict[str, int | bool] = {
        "enabled": True,
        "stores": 0,
        "sources": 0,
        "signals": 0,
        "new": 0,
        "skipped": 0,
        "errors": 0,
    }

    with get_db() as session:
        stores = session.query(Store).filter_by(active=True).all()
        stats["stores"] = len(stores)
        rate_limiter = RateLimiter()

        for store in stores:
            configs = _collect_configs(session, store)
            if not configs:
                continue

            configs_by_tier: dict[int, list[SourceConfig]] = {}
            for cfg in configs:
                configs_by_tier.setdefault(cfg.tier, []).append(cfg)

            success = False
            for tier in sorted(configs_by_tier.keys()):
                for cfg in configs_by_tier[tier]:
                    adapter = _build_adapter(store, cfg, rate_limiter)
                    if adapter is None:
                        continue
                    stats["sources"] += 1
                    try:
                        signals = adapter.discover()
                        if not signals:
                            _mark_failure(cfg, session)
                            continue

                        stats["signals"] += len(signals)
                        new_count, skipped_count = _persist_signals(
                            session, store, signals, max_new=store.max_requests_per_run
                        )
                        stats["new"] += new_count
                        stats["skipped"] += skipped_count
                        _mark_success(cfg, session)
                        success = True
                        break
                    except AdapterError as exc:
                        logger.warning("Adapter failed", store=store.slug, source=cfg.source_type, error=str(exc))
                        stats["errors"] += 1
                        _mark_failure(cfg, session)
                    except Exception as exc:
                        logger.exception("Adapter exception", store=store.slug, source=cfg.source_type)
                        stats["errors"] += 1
                        _mark_failure(cfg, session)
                if success:
                    break

    return stats


def _collect_configs(session: Session, store: Store) -> list[SourceConfig]:
    configs: list[SourceConfig] = [cfg for cfg in store.source_configs if cfg.active]

    # Auto-add browser fallback for category configs that require it.
    for cfg in list(configs):
        if cfg.source_type == "category" and cfg.config_json.get("require_browser"):
            browser_cfg = SourceConfig(
                store_id=store.id,
                source_type="browser",
                tier=4,
                config_key=cfg.config_key,
                config_json={"url": cfg.config_json.get("url")},
                active=True,
            )
            configs.append(browser_cfg)

    legacy_sources = (
        session.query(StoreSource)
        .filter(
            StoreSource.store_id == store.id,
            StoreSource.active == True,  # noqa: E712
            StoreSource.source_type.in_(LEGACY_WEB_SOURCE_TYPES),
        )
        .all()
    )

    for source in legacy_sources:
        cfg = SourceConfig(
            store_id=store.id,
            source_type="category",
            tier=3,
            config_key=source.pattern,
            config_json={"url": source.pattern},
            active=True,
        )
        configs.append(cfg)

    return configs


def _build_adapter(store: Store, cfg: SourceConfig, rate_limiter: RateLimiter):
    crawl_delay = store.crawl_delay_seconds
    source_type = cfg.source_type

    if source_type == "sitemap":
        return SitemapAdapter(store.id, store.name, store.category, cfg.config_json, rate_limiter, crawl_delay)
    if source_type == "rss":
        return RssAdapter(store.id, store.name, cfg.config_json, rate_limiter, crawl_delay)
    if source_type == "json":
        return JsonEndpointAdapter(store.id, cfg.config_json, rate_limiter, crawl_delay)
    if source_type == "category":
        return CategoryPageAdapter(store.id, store.name, store.category, cfg.config_json, rate_limiter, crawl_delay)
    if source_type == "browser":
        return BrowserAdapter(store.id, store.name, store.category, cfg.config_json, rate_limiter, crawl_delay)
    return None


def _signal_message_id(signal: RawSignal, body_hash: str) -> str:
    base = signal.url or signal.metadata.get("id") or f"{signal.source_type}:{signal.store_id}"
    key = hashlib.sha256(str(base).encode("utf-8")).hexdigest()[:16]
    return f"{signal.source_type}:{key}:{body_hash[:16]}"


def _persist_signals(
    session: Session, store: Store, signals: list[RawSignal], max_new: int | None = None
) -> tuple[int, int]:
    new_count = 0
    skipped_count = 0

    for signal in signals:
        if max_new is not None and new_count >= max_new:
            break
        body_text = signal.payload
        body_hash = compute_body_hash(body_text)
        message_id = _signal_message_id(signal, body_hash)

        existing = session.query(EmailRaw).filter_by(gmail_message_id=message_id).first()
        if existing:
            skipped_count += 1
            continue

        payload = prepare_payload(body_text)
        ensure_blob_record(session, payload)

        subject = f"[{signal.source_type.upper()}] {store.name}: {signal.metadata.get('title') or 'Signal'}"
        received_at = signal.observed_at or datetime.now(UTC)

        email = EmailRaw(
            gmail_message_id=message_id,
            gmail_thread_id=None,
            store_id=store.id,
            from_address="crawler@dealintel.local",
            from_domain="dealintel.local",
            from_name="DealIntel Crawler",
            subject=subject,
            received_at=received_at,
            body_text=payload.body_text,
            body_hash=body_hash,
            payload_ref=payload.payload_ref,
            payload_sha256=payload.payload_sha256,
            payload_size_bytes=payload.payload_size_bytes,
            payload_truncated=payload.payload_truncated,
            top_links=signal.metadata.get("top_links"),
            extraction_status="pending",
        )
        session.add(email)
        new_count += 1

    return new_count, skipped_count


def _mark_success(cfg: SourceConfig, session: Session) -> None:
    if cfg.id is None:
        return
    persisted = session.query(SourceConfig).filter_by(id=cfg.id).first()
    if persisted:
        persisted.last_successful_run = datetime.now(UTC)
        persisted.failure_count = 0


def _mark_failure(cfg: SourceConfig, session: Session) -> None:
    if cfg.id is None:
        return
    persisted = session.query(SourceConfig).filter_by(id=cfg.id).first()
    if persisted:
        persisted.failure_count = (persisted.failure_count or 0) + 1
