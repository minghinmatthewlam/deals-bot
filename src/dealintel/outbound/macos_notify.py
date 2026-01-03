"""macOS notification helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import structlog

from dealintel.config import settings

logger = structlog.get_logger()


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _notify_with_osascript(title: str, message: str, subtitle: str | None = None) -> tuple[bool, str | None]:
    parts = [f'display notification "{_escape_applescript(message)}"']
    parts.append(f'with title "{_escape_applescript(title)}"')
    if subtitle:
        parts.append(f'subtitle "{_escape_applescript(subtitle)}"')
    script = " ".join(parts)
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning("osascript notification failed", stderr=result.stderr.strip())
            return False, result.stderr.strip() or "osascript_failed"
        return True, None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("osascript notification error", error=str(exc))
        return False, str(exc)


def _notify_with_terminal_notifier(
    title: str,
    message: str,
    subtitle: str | None = None,
    open_path: Path | None = None,
) -> tuple[bool, str | None]:
    cmd = ["terminal-notifier", "-title", title, "-message", message]
    if subtitle:
        cmd += ["-subtitle", subtitle]
    if open_path:
        cmd += ["-open", open_path.as_uri()]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning("terminal-notifier failed", stderr=result.stderr.strip())
            return False, result.stderr.strip() or "terminal-notifier_failed"
        return True, None
    except FileNotFoundError:
        return False, "terminal-notifier_missing"
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("terminal-notifier error", error=str(exc))
        return False, str(exc)


def send_macos_notification(
    title: str,
    message: str,
    subtitle: str | None = None,
    open_path: Path | None = None,
) -> dict[str, str | bool | None]:
    """Send a macOS notification via terminal-notifier or osascript."""
    mode = settings.notify_macos_mode.strip().lower()
    wants_terminal = mode in {"auto", "terminal-notifier"}
    has_terminal = shutil.which("terminal-notifier") is not None

    if wants_terminal and has_terminal:
        ok, error = _notify_with_terminal_notifier(title, message, subtitle, open_path)
        return {"ok": ok, "method": "terminal-notifier", "error": error}

    if mode == "terminal-notifier" and not has_terminal:
        return {"ok": False, "method": "terminal-notifier", "error": "terminal-notifier_missing"}

    ok, error = _notify_with_osascript(title, message, subtitle)
    return {"ok": ok, "method": "osascript", "error": error}
