"""Newsletter subscription automation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from dealintel.browser.runner import BrowserRunner
from dealintel.config import settings
from dealintel.db import get_db
from dealintel.human_assist import HumanAssistQueue
from dealintel.models import NewsletterSubscription, SourceConfig, Store

logger = structlog.get_logger()


@dataclass(frozen=True)
class SubscribeResult:
    ok: bool
    status: str
    message: str | None = None


class NewsletterAgent:
    def __init__(self, service_email: str | None = None) -> None:
        self.service_email = service_email or settings.newsletter_service_email or settings.sender_email
        self.runner = BrowserRunner()
        self.queue = HumanAssistQueue()

    def subscribe_all(self) -> dict[str, int]:
        stats = {"attempted": 0, "submitted": 0, "confirmed": 0, "failed": 0}

        with get_db() as session:
            configs = (
                session.query(SourceConfig)
                .filter_by(source_type="newsletter", active=True)
                .all()
            )

            for config in configs:
                store = session.query(Store).filter_by(id=config.store_id).first()
                if not store:
                    continue

                stats["attempted"] += 1
                result = self.subscribe_store(session, store, config.config_json)
                if result.ok and result.status == "awaiting_confirmation":
                    stats["submitted"] += 1
                elif result.ok and result.status == "confirmed":
                    stats["confirmed"] += 1
                else:
                    stats["failed"] += 1

        return stats

    def subscribe_store(self, session, store: Store, config: dict[str, Any]) -> SubscribeResult:
        signup_url = config.get("signup_url") or config.get("url")
        if not signup_url:
            return SubscribeResult(ok=False, status="failed", message="missing signup_url")

        subscription = (
            session.query(NewsletterSubscription)
            .filter_by(store_id=store.id, email_address=self.service_email)
            .first()
        )

        if subscription and subscription.status == "confirmed":
            return SubscribeResult(ok=True, status="confirmed")

        if not subscription:
            subscription = NewsletterSubscription(
                store_id=store.id,
                email_address=self.service_email,
                status="pending",
                state="DISCOVERED_SIGNUP_URL",
            )
            session.add(subscription)
            session.flush()

        try:
            self._submit_form(signup_url, config)
        except PlaywrightError as exc:
            subscription.status = "failed"
            subscription.state = "FAILED_NEEDS_HUMAN"
            subscription.last_error = str(exc)
            subscription.last_attempt_at = datetime.now(UTC)
            return SubscribeResult(ok=False, status="failed", message=str(exc))

        subscription.status = "pending"
        subscription.state = "AWAITING_CONFIRMATION_EMAIL" if config.get("expected_confirm", True) else "SUBSCRIBED_CONFIRMED"
        subscription.subscribed_at = datetime.now(UTC)
        subscription.last_attempt_at = datetime.now(UTC)

        if subscription.state == "SUBSCRIBED_CONFIRMED":
            subscription.status = "confirmed"
            subscription.confirmed_at = datetime.now(UTC)
            return SubscribeResult(ok=True, status="confirmed")

        return SubscribeResult(ok=True, status="awaiting_confirmation")

    def _submit_form(self, signup_url: str, config: dict[str, Any]) -> None:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self.runner.user_data_dir),
                headless=self.runner.headless,
                args=self.runner.args,
                viewport={"width": 1280, "height": 800},
            )
            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                page = context.new_page()
                page.set_default_timeout(self.runner.timeout_ms)
                page.goto(signup_url, wait_until="networkidle")

                if self._detect_captcha(page):
                    screenshot = page.screenshot(full_page=True)
                    self.queue.enqueue(kind="captcha", screenshot=screenshot, context={"url": signup_url})
                    raise PlaywrightError("Captcha detected; queued for human assist")

                email_selector = config.get("email_selector")
                if email_selector:
                    locator = page.locator(email_selector)
                else:
                    locator = page.locator("input[type='email']")
                    if locator.count() == 0:
                        locator = page.locator("input[name*='email' i]")

                if locator.count() == 0:
                    raise PlaywrightError("Email field not found")

                locator.first.fill(self.service_email)

                submit_selector = config.get("submit_selector")
                if submit_selector:
                    submit = page.locator(submit_selector)
                else:
                    submit = page.locator("button[type='submit']")
                    if submit.count() == 0:
                        submit = page.get_by_role("button", name="subscribe")

                if submit.count() == 0:
                    raise PlaywrightError("Submit button not found")

                submit.first.click()
                page.wait_for_timeout(2000)

                trace_path = self.runner.trace_dir / f"newsletter_trace_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.zip"
                context.tracing.stop(path=str(trace_path))
            finally:
                context.close()

    def _detect_captcha(self, page) -> bool:
        try:
            content = page.content().lower()
            if "captcha" in content or "recaptcha" in content:
                return True
            for frame in page.frames:
                if frame.url and "recaptcha" in frame.url:
                    return True
            return False
        except Exception:
            return False
