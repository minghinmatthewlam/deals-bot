"""SendGrid email delivery."""

from datetime import datetime

import structlog
from sendgrid import SendGridAPIClient  # type: ignore[import-untyped]
from sendgrid.helpers.mail import Mail  # type: ignore[import-untyped]

from dealintel.config import settings

logger = structlog.get_logger()


def send_digest_email(html: str) -> tuple[bool, str | None]:
    """Send digest email via SendGrid.

    Returns:
        tuple of (success, message_id)
        (True, message_id) on success
        (False, None) on failure
    """
    sg = SendGridAPIClient(settings.sendgrid_api_key.get_secret_value())

    subject = f"Deal Digest - {datetime.now().strftime('%B %d')}"

    message = Mail(
        from_email=settings.sender_email,
        to_emails=settings.recipient_email,
        subject=subject,
        html_content=html,
    )

    try:
        response = sg.send(message)
        message_id = response.headers.get("X-Message-Id")

        logger.info(
            "Digest email sent",
            status_code=response.status_code,
            message_id=message_id,
        )

        return True, message_id

    except Exception as e:
        logger.error("SendGrid error", error=str(e))
        return False, None
