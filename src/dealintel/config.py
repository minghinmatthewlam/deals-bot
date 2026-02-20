"""Configuration management using Pydantic Settings."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+psycopg://dealintel:dealintel_dev@localhost:5432/dealintel"

    # OpenAI
    openai_api_key: SecretStr
    openai_model: str = "gpt-4o-mini"

    # SendGrid
    sendgrid_api_key: SecretStr | None = Field(default=None, validation_alias="SENDGRID_API_KEY")

    # Email addresses
    sender_email: str | None = Field(default=None, validation_alias="DIGEST_FROM_EMAIL")
    recipient_email: str | None = Field(default=None, validation_alias="DIGEST_RECIPIENT")

    # Source toggles
    ingest_gmail: bool = False
    ingest_web: bool = True
    ingest_inbound: bool = False
    ingest_ignore_robots: bool = True

    # Notifications
    notify_email: bool = False
    notify_macos: bool = True
    notify_macos_mode: str = "auto"  # auto | terminal-notifier | osascript
    notify_telegram: bool = True
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # Web crawl defaults
    web_default_crawl_delay_seconds: float = 30.0
    web_default_max_requests_per_run: int | None = None

    # Payload storage
    payload_max_inline_bytes: int = 200_000
    payload_blob_dir: str = "~/.deals-bot/payloads"

    # Browser automation (Playwright)
    browser_user_data_dir: str = "~/.deals-bot/browser-profile"
    browser_headless: bool = False
    browser_timeout_ms: int = 30_000
    browser_args: list[str] = [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
    ]
    browser_artifacts_dir: str = "~/.deals-bot/browser-artifacts"
    browser_trace_dir: str = "~/.deals-bot/browser-traces"

    # Human assist queue
    human_assist_dir: str = "~/.deals-bot/human-assist"
    human_assist_retention_days: int = 14

    # Newsletter automation
    newsletter_service_email: str | None = None

    # Clawdbot integration (optional - falls back to Playwright if unavailable)
    clawdbot_enabled: bool = False
    clawdbot_gateway_url: str = "ws://127.0.0.1:18789"
    clawdbot_token: str | None = None
    clawdbot_timeout_seconds: int = 180  # 3 minutes for interactive tasks

    # Gmail OAuth
    gmail_credentials_path: str = "credentials.json"
    gmail_token_path: str = "token.json"
    gmail_lookback_days: int = 14
    gmail_max_messages: int | None = None

    # Extraction limits
    extract_max_emails: int | None = None


settings = Settings()  # type: ignore[call-arg]
