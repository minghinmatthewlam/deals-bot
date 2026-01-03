"""Notification fan-out for digests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dealintel.config import settings
from dealintel.outbound.macos_notify import send_macos_notification
from dealintel.outbound.telegram_client import send_telegram_message


def _format_digest_lines(items: list[dict[str, Any]], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for item in items[:limit]:
        store = item.get("store") or "Store"
        headline = item.get("headline") or "Promo"
        badge = item.get("badge") or ""
        source = item.get("source_type") or ""
        suffix = " · ".join(part for part in [badge, source] if part)
        if suffix:
            lines.append(f"- {store}: {headline} ({suffix})")
        else:
            lines.append(f"- {store}: {headline}")
    return lines


def send_digest_notifications(
    *,
    date_label: str,
    promo_count: int,
    store_count: int,
    items: list[dict[str, Any]],
    html_path: Path | None,
) -> dict[str, Any]:
    summary = f"DealIntel digest ({date_label}): {promo_count} promos from {store_count} stores."
    detail_lines = _format_digest_lines(items)
    telegram_text = summary
    if detail_lines:
        telegram_text += "\n" + "\n".join(detail_lines)
    resolved_path = html_path.resolve() if html_path else None
    if resolved_path:
        telegram_text += f"\n\nDigest saved locally: {resolved_path}"

    results: dict[str, Any] = {"summary": summary}

    if settings.notify_macos:
        results["macos"] = send_macos_notification(
            title="DealIntel Digest",
            message=summary,
            subtitle="Weekly sales update",
            open_path=resolved_path,
        )
    else:
        results["macos"] = {"ok": False, "error": "disabled", "method": None}

    if settings.notify_telegram:
        results["telegram"] = send_telegram_message(telegram_text)
    else:
        results["telegram"] = {"ok": False, "error": "disabled", "message_id": None}

    return results
