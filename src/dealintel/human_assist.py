"""Human-in-the-loop task queue for captcha/automation failures."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from dealintel.config import settings


@dataclass(frozen=True)
class AssistTask:
    task_id: str
    path: Path


class HumanAssistQueue:
    def __init__(self) -> None:
        self.base_dir = Path(settings.human_assist_dir).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def enqueue(self, *, kind: str, screenshot: bytes | None, context: dict[str, object]) -> AssistTask:
        task_id = uuid4().hex
        task_path = self.base_dir / task_id
        task_path.mkdir(parents=True, exist_ok=True)

        if screenshot:
            (task_path / "screenshot.png").write_bytes(screenshot)

        payload = {
            "kind": kind,
            "context": context,
            "created_at": datetime.now(UTC).isoformat(),
        }
        (task_path / "context.json").write_text(json.dumps(payload, indent=2))
        (task_path / "solution.txt").write_text("")

        return AssistTask(task_id=task_id, path=task_path)

    def wait_for_solution(self, task: AssistTask, timeout_seconds: int = 3600) -> str | None:
        deadline = time.time() + timeout_seconds
        solution_path = task.path / "solution.txt"
        while time.time() < deadline:
            content = solution_path.read_text().strip()
            if content:
                return content
            time.sleep(5)
        return None

    def cleanup(self) -> int:
        retention_days = settings.human_assist_retention_days
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        removed = 0

        for task_dir in self.base_dir.iterdir():
            if not task_dir.is_dir():
                continue
            context_path = task_dir / "context.json"
            if not context_path.exists():
                continue
            try:
                data = json.loads(context_path.read_text())
                created_at = data.get("created_at")
                if not created_at:
                    continue
                created_dt = datetime.fromisoformat(created_at)
                if created_dt < cutoff:
                    for child in task_dir.iterdir():
                        child.unlink(missing_ok=True)
                    task_dir.rmdir()
                    removed += 1
            except Exception:
                continue

        return removed
