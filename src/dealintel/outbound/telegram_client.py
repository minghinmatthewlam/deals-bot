"""Telegram notification helpers."""

from __future__ import annotations

import httpx
import structlog

from dealintel.config import settings

logger = structlog.get_logger()


def send_telegram_message(text: str) -> dict[str, str | bool | None]:
    """Send a Telegram message to the configured chat."""
    token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else None
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return {"ok": False, "error": "telegram_config_missing", "message_id": None}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload)
        if response.status_code != 200:
            return {
                "ok": False,
                "error": f"telegram_http_{response.status_code}",
                "message_id": None,
            }
        data = response.json()
        if not data.get("ok"):
            return {
                "ok": False,
                "error": str(data.get("description") or "telegram_error"),
                "message_id": None,
            }
        return {
            "ok": True,
            "error": None,
            "message_id": str((data.get("result") or {}).get("message_id")),
        }
    except Exception as exc:  # pragma: no cover - network failure
        logger.warning("Telegram send failed", error=str(exc))
        return {"ok": False, "error": str(exc), "message_id": None}
