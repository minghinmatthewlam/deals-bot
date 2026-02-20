"""Newsletter subscription automation."""

from __future__ import annotations

import asyncio
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
from dealintel.prefs import get_store_allowlist

logger = structlog.get_logger()


# Clawdbot agent prompt template
CLAWDBOT_SUBSCRIBE_PROMPT = """
I need you to sign up for a newsletter. Here are the details:

**Store**: {store_name}
**Signup URL**: {signup_url}
**Email to use**: {email}

## Instructions

1. Navigate to the signup URL using the browser tool
2. Take a snapshot to see the page structure
3. Find the newsletter form - look for:
   - Email input fields (often in footer, sidebar, or popup)
   - Labels like "Newsletter", "Sign up", "Subscribe", "Get deals"
4. Fill in the email address
5. Check any required checkboxes (consent, terms)
6. Submit the form
7. Take a screenshot to confirm

## Handling Issues

- Close cookie consent banners first
- If newsletter is in a popup, interact with it
- Look for "Subscribe" links in navigation or footer
- Try scrolling to trigger popups

## CAPTCHA

If you encounter a CAPTCHA:
1. Take a screenshot showing it
2. Describe what you see
3. Say "CAPTCHA_HELP_NEEDED" and wait for help via messaging

## Output

End with one of:
- `STATUS: SUCCESS` - Signup completed
- `STATUS: CAPTCHA_WAITING` - Need human help
- `STATUS: FORM_NOT_FOUND` - Could not find form
- `STATUS: FAILED - <reason>` - Other failure
"""


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
        self._clawdbot_checked = False
        self._clawdbot_available = False

    def _check_clawdbot(self) -> bool:
        """Check if Clawdbot is available (cached for the session)."""
        if self._clawdbot_checked:
            return self._clawdbot_available

        self._clawdbot_checked = True

        if not settings.clawdbot_enabled:
            logger.debug("clawdbot.disabled")
            self._clawdbot_available = False
            return False

        from dealintel.clawdbot import clawdbot_available

        self._clawdbot_available = clawdbot_available()
        if self._clawdbot_available:
            logger.info("clawdbot.available", url=settings.clawdbot_gateway_url)
        else:
            logger.info("clawdbot.not_running", url=settings.clawdbot_gateway_url)

        return self._clawdbot_available

    def _subscribe_via_clawdbot(self, store: Store, signup_url: str) -> SubscribeResult | None:
        """
        Try to subscribe using Clawdbot agent.

        Returns SubscribeResult if Clawdbot handled it, None to fall back to Playwright.
        """
        if not self._check_clawdbot():
            return None

        prompt = CLAWDBOT_SUBSCRIBE_PROMPT.format(
            store_name=store.name,
            signup_url=signup_url,
            email=self.service_email,
        )

        try:
            from dealintel.clawdbot import ClawdbotClient

            # Run async code in sync context
            result = asyncio.get_event_loop().run_until_complete(
                self._run_clawdbot_agent(prompt)
            )
            return result
        except RuntimeError:
            # No event loop - create one
            result = asyncio.run(self._run_clawdbot_agent(prompt))
            return result
        except Exception as e:
            logger.warning("clawdbot.error", error=str(e), store=store.name)
            return None  # Fall back to Playwright

    async def _run_clawdbot_agent(self, prompt: str) -> SubscribeResult | None:
        """Run the Clawdbot agent and parse the result."""
        from dealintel.clawdbot import ClawdbotClient

        try:
            async with ClawdbotClient() as client:
                result = await client.run_agent(prompt)
        except ConnectionError as e:
            logger.warning("clawdbot.connection_failed", error=str(e))
            return None

        if not result.success:
            if result.error and "timeout" in result.error.lower():
                return SubscribeResult(ok=False, status="failed", message=f"Clawdbot timeout: {result.error}")
            logger.warning("clawdbot.agent_failed", error=result.error)
            return None  # Fall back to Playwright

        # Parse agent response
        response_lower = result.response.lower()

        if "status: success" in response_lower:
            return SubscribeResult(ok=True, status="awaiting_confirmation")

        if "status: captcha_waiting" in response_lower or "captcha_help_needed" in response_lower:
            # Clawdbot will handle human-in-loop via WhatsApp/Telegram
            return SubscribeResult(ok=False, status="failed", message="CAPTCHA - Clawdbot awaiting human help")

        if "status: form_not_found" in response_lower:
            return SubscribeResult(ok=False, status="failed", message="Form not found by Clawdbot agent")

        if "status: failed" in response_lower:
            return SubscribeResult(ok=False, status="failed", message=f"Clawdbot: {result.response[:200]}")

        # Infer from content
        if any(word in response_lower for word in ["success", "subscribed", "signed up"]):
            return SubscribeResult(ok=True, status="awaiting_confirmation")

        # Unclear result - fall back to Playwright
        logger.warning("clawdbot.unclear_result", response=result.response[:200])
        return None

    def subscribe_all(self) -> dict[str, int]:
        stats = {"attempted": 0, "submitted": 0, "confirmed": 0, "failed": 0}

        with get_db() as session:
            configs = session.query(SourceConfig).filter_by(source_type="newsletter", active=True).all()
            allowlist = get_store_allowlist()

            for config in configs:
                store = session.query(Store).filter_by(id=config.store_id).first()
                if not store:
                    continue
                if allowlist and store.slug not in allowlist:
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
            session.query(NewsletterSubscription).filter_by(store_id=store.id, email_address=self.service_email).first()
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

        # Try Clawdbot first if enabled and running
        clawdbot_result = self._subscribe_via_clawdbot(store, signup_url)
        if clawdbot_result is not None:
            # Clawdbot handled it (success or definitive failure)
            logger.info("newsletter.clawdbot_handled", store=store.name, ok=clawdbot_result.ok)
            if clawdbot_result.ok:
                subscription.status = "pending"
                subscription.state = "AWAITING_CONFIRMATION_EMAIL"
                subscription.subscribed_at = datetime.now(UTC)
                subscription.last_attempt_at = datetime.now(UTC)
                return clawdbot_result
            else:
                subscription.status = "failed"
                subscription.state = "FAILED_NEEDS_HUMAN"
                subscription.last_error = clawdbot_result.message
                subscription.last_attempt_at = datetime.now(UTC)
                return clawdbot_result

        # Fall back to Playwright
        logger.info("newsletter.playwright_fallback", store=store.name)
        try:
            self._submit_form(signup_url, config)
        except PlaywrightError as exc:
            subscription.status = "failed"
            subscription.state = "FAILED_NEEDS_HUMAN"
            subscription.last_error = str(exc)
            subscription.last_attempt_at = datetime.now(UTC)
            return SubscribeResult(ok=False, status="failed", message=str(exc))

        subscription.status = "pending"
        subscription.state = (
            "AWAITING_CONFIRMATION_EMAIL" if config.get("expected_confirm", True) else "SUBSCRIBED_CONFIRMED"
        )
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
                timeout_ms = config.get("timeout_ms") or self.runner.timeout_ms
                wait_until = config.get("wait_until") or "networkidle"
                page.set_default_timeout(timeout_ms)
                page.goto(signup_url, wait_until=wait_until)

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

                trace_path = (
                    self.runner.trace_dir / f"newsletter_trace_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.zip"
                )
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
