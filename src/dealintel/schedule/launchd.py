"""Launchd scheduling helpers for macOS."""

from __future__ import annotations

import plistlib
import re
import subprocess
from pathlib import Path

import structlog

logger = structlog.get_logger()


DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
PLIST_NAME = "com.dealintel.weekly.plist"
JOB_LABEL = "com.dealintel.weekly"


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

    plist_path = agents_dir / PLIST_NAME
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
    subprocess.run(["launchctl", "start", JOB_LABEL], check=False)


def uninstall_weekly_launchd() -> dict[str, str | bool | None]:
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = agents_dir / PLIST_NAME
    if not plist_path.exists():
        return {"ok": False, "error": "not_installed"}
    subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
    plist_path.unlink(missing_ok=True)
    return {"ok": True, "error": None}


def get_weekly_status() -> dict[str, str | int | bool | None]:
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = agents_dir / PLIST_NAME
    if not plist_path.exists():
        return {"installed": False}

    payload = {"installed": True, "plist_path": str(plist_path)}
    try:
        plist_data = plistlib.loads(plist_path.read_bytes())
        schedule = plist_data.get("StartCalendarInterval", {})
        payload["weekday"] = schedule.get("Weekday")
        payload["hour"] = schedule.get("Hour")
        payload["minute"] = schedule.get("Minute")
    except Exception:
        pass

    uid = subprocess.run(["id", "-u"], check=True, capture_output=True, text=True).stdout.strip()
    result = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{JOB_LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        payload["loaded"] = False
        payload["error"] = result.stderr.strip() or "launchctl_print_failed"
        return payload

    output = result.stdout
    payload["loaded"] = True
    state_match = re.search(r"state = ([^\n]+)", output)
    pid_match = re.search(r"\bpid = (\\d+)", output)
    exit_match = re.search(r"last exit code = (\\d+)", output)
    runs_match = re.search(r"runs = (\\d+)", output)
    if state_match:
        payload["state"] = state_match.group(1).strip()
    if pid_match:
        payload["pid"] = int(pid_match.group(1))
    if exit_match:
        payload["last_exit_code"] = int(exit_match.group(1))
    if runs_match:
        payload["runs"] = int(runs_match.group(1))
    return payload
