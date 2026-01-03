"""Launchd scheduling helpers for macOS."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import structlog

logger = structlog.get_logger()


DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _resolve_program_args(repo_path: Path) -> list[str]:
    dealintel_bin = repo_path / ".venv" / "bin" / "dealintel"
    if dealintel_bin.exists():
        return [str(dealintel_bin), "weekly"]
    python_bin = repo_path / ".venv" / "bin" / "python"
    if python_bin.exists():
        return [str(python_bin), "-m", "dealintel.cli", "weekly"]
    return [str(repo_path / ".venv" / "bin" / "dealintel"), "weekly"]


def build_weekly_plist(
    repo_path: Path,
    logs_dir: Path,
    hour: int,
    minute: int,
    weekday: int,
) -> bytes:
    payload = {
        "Label": "com.dealintel.weekly",
        "ProgramArguments": _resolve_program_args(repo_path),
        "WorkingDirectory": str(repo_path),
        "StartCalendarInterval": {
            "Weekday": weekday,
            "Hour": hour,
            "Minute": minute,
        },
        "EnvironmentVariables": {"PATH": DEFAULT_PATH},
        "StandardOutPath": str(logs_dir / "weekly.log"),
        "StandardErrorPath": str(logs_dir / "weekly.err"),
        "RunAtLoad": False,
        "KeepAlive": False,
        "Nice": 5,
    }
    return plistlib.dumps(payload)


def install_weekly_launchd(
    repo_path: Path,
    hour: int,
    minute: int,
    weekday: int,
    load: bool = True,
) -> Path:
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = repo_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    plist_path = agents_dir / "com.dealintel.weekly.plist"
    plist_bytes = build_weekly_plist(repo_path, logs_dir, hour, minute, weekday)
    plist_path.write_bytes(plist_bytes)

    if load:
        _reload_launchd(plist_path)

    return plist_path


def _reload_launchd(plist_path: Path) -> None:
    subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
    result = subprocess.run(["launchctl", "load", str(plist_path)], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("launchctl load failed", stderr=result.stderr.strip())


def run_now() -> None:
    subprocess.run(["launchctl", "start", "com.dealintel.weekly"], check=False)
