"""Gmail OAuth 2.0 authentication."""

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from dealintel.config import settings

# Read-only Gmail access
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_credentials() -> Credentials:
    """Load or refresh Gmail OAuth credentials."""
    creds = None
    token_path = Path(settings.gmail_token_path)

    # Load existing token
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    # Refresh or get new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(settings.gmail_credentials_path).exists():
                raise FileNotFoundError(
                    f"Gmail credentials not found at {settings.gmail_credentials_path}. "
                    "Download from Google Cloud Console."
                )

            flow = InstalledAppFlow.from_client_secrets_file(settings.gmail_credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save token for next run
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


def run_oauth_flow() -> None:
    """Run OAuth flow interactively (for CLI command)."""
    get_credentials()
    print("Gmail authentication successful!")
