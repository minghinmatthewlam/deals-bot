"""Notification fan-out for digests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from dealintel.config import settings
from dealintel.outbound.macos_notify import send_macos_notification
from dealintel.outbound.sendgrid_client import send_digest_email
from dealintel.outbound.telegram_client import send_telegram_message


@dataclass(frozen=True)
class DigestNotification:
    date_label: str
    promo_count: int
    store_count: int
    items: list[dict[str, Any]]
    html_path: Path | None

    def summary(self) -> str:
        return f"DealIntel digest ({self.date_label}): {self.promo_count} promos from {self.store_count} stores."

    def detail_lines(self, limit: int = 8) -> list[str]:
        lines: list[str] = []
        for item in self.items[:limit]:
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

    def resolved_path(self) -> Path | None:
        return self.html_path.resolve() if self.html_path else None

    def telegram_message(self) -> str:
        summary = self.summary()
        detail_lines = self.detail_lines()
        if detail_lines:
            summary += "\n" + "\n".join(detail_lines)
        resolved = self.resolved_path()
        if resolved:
            summary += f"\n\nDigest saved locally: {resolved}"
        return summary


class NotificationChannel(Protocol):
    name: str

    def send(self, payload: DigestNotification, html: str | None) -> dict[str, Any]:
        """Send a notification and return a structured result."""


class MacOSNotificationChannel:
    name = "macos"

    def send(self, payload: DigestNotification, html: str | None) -> dict[str, Any]:
        if not settings.notify_macos:
            return {"ok": False, "error": "disabled", "method": None}
        return send_macos_notification(
            title="DealIntel Digest",
            message=payload.summary(),
            subtitle="Weekly sales update",
            open_path=payload.resolved_path(),
        )


class TelegramNotificationChannel:
    name = "telegram"

    def send(self, payload: DigestNotification, html: str | None) -> dict[str, Any]:
        if not settings.notify_telegram:
            return {"ok": False, "error": "disabled", "message_id": None}
        return send_telegram_message(payload.telegram_message())


class EmailNotificationChannel:
    name = "email"

    def send(self, payload: DigestNotification, html: str | None) -> dict[str, Any]:
        if not settings.notify_email:
            return {"ok": False, "error": "disabled", "message_id": None}
        if not html:
            return {"ok": False, "error": "missing_html", "message_id": None}
        success, message_id = send_digest_email(html)
        return {"ok": success, "error": None if success else "send_failed", "message_id": message_id}


def deliver_digest_notifications(
    payload: DigestNotification,
    html: str | None,
    channels: list[NotificationChannel] | None = None,
) -> dict[str, Any]:
    """Send digest notifications through configured channels."""
    if channels is None:
        channels = [
            MacOSNotificationChannel(),
            TelegramNotificationChannel(),
            EmailNotificationChannel(),
        ]

    results: dict[str, Any] = {"summary": payload.summary()}
    delivered = False
    email_message_id: str | None = None

    for channel in channels:
        result = channel.send(payload, html)
        results[channel.name] = result
        if result.get("ok"):
            delivered = True
        if channel.name == "email" and result.get("message_id"):
            email_message_id = str(result.get("message_id"))

    results["delivered"] = delivered
    if email_message_id:
        results["email_message_id"] = email_message_id
    return results
