"""Configuration management using Pydantic Settings."""

from pydantic import SecretStr
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
    sendgrid_api_key: SecretStr

    # Email addresses
    sender_email: str
    recipient_email: str

    # Gmail OAuth
    gmail_credentials_path: str = "credentials.json"
    gmail_token_path: str = "token.json"


settings = Settings()
