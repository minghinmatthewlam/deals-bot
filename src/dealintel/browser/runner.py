"""Playwright browser runner with artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from dealintel.config import settings

logger = structlog.get_logger()


@dataclass(frozen=True)
class BrowserResult:
    url: str
    html: str | None
    title: str | None
    screenshot_path: str | None
    trace_path: str | None
    error: str | None
    captcha_detected: bool


class BrowserRunner:
    def __init__(self) -> None:
        self.user_data_dir = Path(settings.browser_user_data_dir).expanduser()
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir = Path(settings.browser_artifacts_dir).expanduser()
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.trace_dir = Path(settings.browser_trace_dir).expanduser()
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.headless = settings.browser_headless
        self.timeout_ms = settings.browser_timeout_ms
        self.args = settings.browser_args

    def fetch_page(
        self,
        url: str,
        wait_selector: str | None = None,
        wait_until: str | None = None,
        timeout_ms: int | None = None,
        *,
        capture_screenshot_on_success: bool = False,
    ) -> BrowserResult:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        trace_path = str(self.trace_dir / f"trace_{timestamp}.zip")
        screenshot_path = str(self.artifacts_dir / f"screenshot_{timestamp}.png")

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                args=self.args,
                viewport={"width": 1280, "height": 800},
            )

            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                page = context.new_page()
                page.set_default_timeout(timeout_ms or self.timeout_ms)
                page.goto(url, wait_until=wait_until or "networkidle")
                if wait_selector:
                    page.wait_for_selector(wait_selector)
                html = page.content()
                title = page.title()
                captcha_detected = self._detect_captcha(page)
                success_screenshot = None
                if capture_screenshot_on_success:
                    page.screenshot(path=screenshot_path, full_page=True)
                    success_screenshot = screenshot_path

                context.tracing.stop(path=trace_path)

                return BrowserResult(
                    url=url,
                    html=html,
                    title=title,
                    screenshot_path=success_screenshot,
                    trace_path=trace_path,
                    error=None,
                    captcha_detected=captcha_detected,
                )
            except PlaywrightError as exc:
                try:
                    page = context.pages[0] if context.pages else None
                    if page:
                        page.screenshot(path=screenshot_path, full_page=True)
                except Exception:
                    logger.exception("Failed to capture screenshot")
                try:
                    context.tracing.stop(path=trace_path)
                except Exception:
                    logger.exception("Failed to save trace")

                return BrowserResult(
                    url=url,
                    html=None,
                    title=None,
                    screenshot_path=screenshot_path,
                    trace_path=trace_path,
                    error=str(exc),
                    captcha_detected=self._detect_captcha(context.pages[0]) if context.pages else False,
                )
            finally:
                context.close()

    def _detect_captcha(self, page) -> bool:
        try:
            content = page.content().lower()
            if "captcha" in content or "recaptcha" in content:
                return True
            frames = page.frames
            for frame in frames:
                if frame.url and "recaptcha" in frame.url:
                    return True
            return False
        except Exception:
            return False
