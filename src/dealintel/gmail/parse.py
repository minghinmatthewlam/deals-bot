"""Email parsing utilities."""

import base64
import hashlib
import re
from email.utils import parseaddr
from typing import Any

import html2text
from bs4 import BeautifulSoup


def parse_headers(message: dict[str, Any]) -> dict[str, str]:
    """Extract headers from Gmail message."""
    headers = {}
    for header in message.get("payload", {}).get("headers", []):
        headers[header["name"]] = header["value"]
    return headers


def parse_from_address(from_header: str) -> tuple[str, str | None]:
    """Extract email address and display name from From header.

    Returns:
        tuple of (email_address, display_name or None)
    """
    name, email = parseaddr(from_header)
    return email.lower(), name if name else None


def get_body_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Recursively extract body parts from message payload."""
    parts = []

    if "body" in payload and payload["body"].get("data"):
        parts.append(
            {
                "mimeType": payload.get("mimeType", ""),
                "data": payload["body"]["data"],
            }
        )

    for part in payload.get("parts", []):
        parts.extend(get_body_parts(part))

    return parts


def parse_body(message: dict[str, Any]) -> tuple[str | None, list[str] | None]:
    """Extract text body and top links from Gmail message.

    Returns:
        tuple of (body_text, top_links)
    """
    payload = message.get("payload", {})
    parts = get_body_parts(payload)

    # Prefer text/plain, fallback to text/html
    text_part = None
    html_part = None

    for part in parts:
        mime_type = part.get("mimeType", "")
        if mime_type == "text/plain" and not text_part:
            text_part = part
        elif mime_type == "text/html" and not html_part:
            html_part = part

    body_text = None
    top_links = []

    if text_part:
        body_text = base64.urlsafe_b64decode(text_part["data"]).decode("utf-8", errors="replace")
    elif html_part:
        html_content = base64.urlsafe_b64decode(html_part["data"]).decode("utf-8", errors="replace")

        # Extract links before converting
        top_links = extract_top_links(html_content)

        # Convert HTML to text
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = True
        converter.body_width = 0  # No wrapping
        body_text = converter.handle(html_content)

    return body_text, top_links if top_links else None


def extract_top_links(html_content: str, limit: int = 10) -> list[str]:
    """Extract first N unique links from HTML."""
    soup = BeautifulSoup(html_content, "html.parser")
    links = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not isinstance(href, str):
            continue
        # Skip mailto, tel, javascript links
        if any(href.startswith(prefix) for prefix in ["mailto:", "tel:", "javascript:", "#"]):
            continue
        # Skip already seen
        if href in seen:
            continue
        seen.add(href)
        links.append(href)
        if len(links) >= limit:
            break

    return links


def compute_body_hash(body_text: str) -> str:
    """Compute SHA256 hash of normalized body text."""
    # Normalize: lowercase, remove extra whitespace
    normalized = re.sub(r"\s+", " ", body_text.lower().strip())
    return hashlib.sha256(normalized.encode()).hexdigest()
