"""Tiered web ingestion pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from dealintel.config import settings
from dealintel.db import get_db
from dealintel.gmail.parse import compute_body_hash
from dealintel.ingest.keys import compute_signal_key, signal_message_id
from dealintel.ingest.signals import RawSignal
from dealintel.models import EmailRaw, RawSignalRecord, SourceConfig, Store, StoreSource
from dealintel.prefs import get_store_allowlist
from dealintel.storage.payloads import ensure_blob_record, prepare_payload
from dealintel.web.adapters.base import AdapterError, SourceResultStatus, SourceTier
from dealintel.web.adapters.browser import BrowserAdapter
from dealintel.web.adapters.category import CategoryPageAdapter
from dealintel.web.adapters.json_endpoint import JsonEndpointAdapter
from dealintel.web.adapters.rss import RssAdapter
from dealintel.web.adapters.sitemap import SitemapAdapter
from dealintel.web.budget import RequestBudget
from dealintel.web.rate_limit import RateLimiter

logger = structlog.get_logger()

LEGACY_WEB_SOURCE_TYPES = {"web_url"}


def ingest_tiered_sources() -> dict[str, int | bool | list]:
    stats: dict[str, int | bool | list] = {
        "enabled": True,
        "stores": 0,
        "sources": 0,
        "signals": 0,
        "new": 0,
        "skipped": 0,
        "errors": 0,
    }
    stats["attempts"] = []

    with get_db() as session:
        allowlist = get_store_allowlist()
        stores = session.query(Store).filter_by(active=True).all()
        if allowlist:
            stores = [store for store in stores if store.slug in allowlist]
        stats["stores"] = len(stores)
        rate_limiter = RateLimiter()

        for store in stores:
            max_requests = store.max_requests_per_run or settings.web_default_max_requests_per_run
            budget = RequestBudget(max_requests=max_requests)
            configs = _collect_configs(session, store)
            if not configs:
                continue

            configs_by_tier: dict[int, list[SourceConfig]] = {}
            for cfg in configs:
                configs_by_tier.setdefault(cfg.tier, []).append(cfg)

            success = False
            for tier in sorted(configs_by_tier.keys()):
                for cfg in configs_by_tier[tier]:
                    adapter = build_adapter(store, cfg, rate_limiter, budget)
                    if adapter is None:
                        continue
                    stats["sources"] += 1
                    try:
                        result = adapter.discover()
                        signals = result.signals

                        attempt = {
                            "store": store.slug,
                            "store_name": store.name,
                            "tier": cfg.tier,
                            "source_type": cfg.source_type,
                            "config_key": cfg.config_key,
                            "status": result.status.value,
                            "error_code": result.error_code,
                            "message": result.message,
                            "http_requests": result.http_requests,
                            "bytes_read": result.bytes_read,
                            "duration_ms": result.duration_ms,
                            "signals": len(signals),
                            "signals_new": 0,
                            "signals_skipped": 0,
                            "sample_urls": result.sample_urls,
                        }

                        _update_fetch_state(cfg, result, session)

                        if result.status == SourceResultStatus.SUCCESS and signals:
                            stats["signals"] += len(signals)
                            new_count, skipped_count = _persist_signals(session, store, signals)
                            stats["new"] += new_count
                            stats["skipped"] += skipped_count
                            attempt["signals_new"] = new_count
                            attempt["signals_skipped"] = skipped_count
                            _mark_success(cfg, session)
                            success = True
                        elif result.status in (SourceResultStatus.FAILURE, SourceResultStatus.ERROR):
                            stats["errors"] += 1
                            _mark_failure(cfg, session)

                        stats["attempts"].append(attempt)

                        if success:
                            break
                    except AdapterError as exc:
                        logger.warning("Adapter failed", store=store.slug, source=cfg.source_type, error=str(exc))
                        stats["errors"] += 1
                        stats["attempts"].append(
                            {
                                "store": store.slug,
                                "store_name": store.name,
                                "tier": cfg.tier,
                                "source_type": cfg.source_type,
                                "config_key": cfg.config_key,
                                "status": SourceResultStatus.FAILURE.value,
                                "error_code": "adapter_error",
                                "message": str(exc),
                                "http_requests": 0,
                                "bytes_read": 0,
                                "duration_ms": None,
                                "signals": 0,
                                "signals_new": 0,
                                "signals_skipped": 0,
                                "sample_urls": [],
                            }
                        )
                        _mark_failure(cfg, session)
                    except Exception as exc:
                        logger.exception("Adapter exception", store=store.slug, source=cfg.source_type)
                        stats["errors"] += 1
                        stats["attempts"].append(
                            {
                                "store": store.slug,
                                "store_name": store.name,
                                "tier": cfg.tier,
                                "source_type": cfg.source_type,
                                "config_key": cfg.config_key,
                                "status": SourceResultStatus.ERROR.value,
                                "error_code": "adapter_exception",
                                "message": str(exc),
                                "http_requests": 0,
                                "bytes_read": 0,
                                "duration_ms": None,
                                "signals": 0,
                                "signals_new": 0,
                                "signals_skipped": 0,
                                "sample_urls": [],
                            }
                        )
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


def build_adapter(
    store: Store,
    cfg: SourceConfig,
    rate_limiter: RateLimiter,
    budget: RequestBudget | None = None,
):
    crawl_delay = store.crawl_delay_seconds
    robots_policy = store.robots_policy
    source_type = cfg.source_type

    if source_type == "sitemap":
        return SitemapAdapter(
            store.id,
            store.name,
            store.category,
            cfg.config_json,
            rate_limiter,
            crawl_delay,
            robots_policy,
            budget,
            etag=cfg.etag,
            last_modified=cfg.last_modified,
        )
    if source_type == "rss":
        return RssAdapter(
            store.id,
            store.name,
            cfg.config_json,
            rate_limiter,
            crawl_delay,
            robots_policy,
            budget,
            etag=cfg.etag,
            last_modified=cfg.last_modified,
        )
    if source_type == "json":
        return JsonEndpointAdapter(
            store.id,
            cfg.config_json,
            rate_limiter,
            crawl_delay,
            robots_policy,
            budget,
            etag=cfg.etag,
            last_modified=cfg.last_modified,
        )
    if source_type == "category":
        return CategoryPageAdapter(
            store.id,
            store.name,
            store.category,
            cfg.config_json,
            rate_limiter,
            crawl_delay,
            robots_policy,
            budget,
            etag=cfg.etag,
            last_modified=cfg.last_modified,
        )
    if source_type == "browser":
        return BrowserAdapter(
            store.id,
            store.name,
            store.category,
            cfg.config_json,
            rate_limiter,
            crawl_delay,
            robots_policy,
            budget,
            etag=cfg.etag,
            last_modified=cfg.last_modified,
        )
    return None


def _persist_signals(session: Session, store: Store, signals: list[RawSignal]) -> tuple[int, int]:
    new_count = 0
    skipped_count = 0

    for signal in signals:
        body_text = signal.payload
        body_hash = compute_body_hash(body_text)
        signal_key = compute_signal_key(signal)
        message_id = signal_message_id(f"{store.id}:{signal_key}", body_hash)

        payload = prepare_payload(body_text)
        ensure_blob_record(session, payload)

        existing_email = (
            session.query(EmailRaw)
            .filter_by(store_id=store.id, signal_key=signal_key, body_hash=body_hash)
            .first()
        )
        existing_signal = (
            session.query(RawSignalRecord)
            .filter_by(store_id=store.id, signal_key=signal_key, payload_sha256=payload.payload_sha256)
            .first()
        )
        if existing_email or existing_signal:
            skipped_count += 1
            continue

        subject = f"[{signal.source_type.upper()}] {store.name}: {signal.metadata.get('title') or 'Signal'}"
        received_at = signal.observed_at or datetime.now(UTC)

        top_links = signal.metadata.get("top_links") or []
        if signal.url:
            if signal.url in top_links:
                top_links.remove(signal.url)
            top_links = [signal.url, *top_links]

        session.add(
            RawSignalRecord(
                store_id=store.id,
                source_type=signal.source_type,
                signal_key=signal_key,
                url=signal.url,
                observed_at=received_at,
                payload_type=signal.payload_type,
                payload_ref=payload.payload_ref,
                payload_sha256=payload.payload_sha256,
                payload_size_bytes=payload.payload_size_bytes,
                payload_truncated=payload.payload_truncated,
                metadata=signal.metadata or {},
            )
        )

        email = EmailRaw(
            gmail_message_id=message_id,
            gmail_thread_id=None,
            store_id=store.id,
            signal_key=signal_key,
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
            top_links=top_links or None,
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


def _update_fetch_state(cfg: SourceConfig, result: Any, session: Session) -> None:
    if cfg.id is None:
        return
    persisted = session.query(SourceConfig).filter_by(id=cfg.id).first()
    if not persisted:
        return
    if result.etag:
        persisted.etag = result.etag
    if result.last_modified:
        persisted.last_modified = result.last_modified
    if result.last_seen_item_at:
        persisted.last_seen_item_at = result.last_seen_item_at
