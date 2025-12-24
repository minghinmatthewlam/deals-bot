"""Parse .eml files into structured data."""

from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

import html2text

from dealintel.gmail.parse import extract_top_links


@dataclass
class ParsedEmail:
    subject: str
    from_address: str
    from_name: str | None
    received_at: datetime | None
    body_text: str | None
    top_links: list[str] | None


def parse_eml(raw_bytes: bytes) -> ParsedEmail:
    """Parse raw .eml bytes into structured data."""
    msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    subject = msg.get("subject") or "(no subject)"
    from_header = msg.get("from") or ""
    from_address, from_name = _parse_from_address(from_header)

    received_at = None
    if msg.get("date"):
        try:
            received_at = parsedate_to_datetime(msg.get("date"))
        except Exception:
            received_at = None

    body_text, top_links = _get_best_body(msg)

    return ParsedEmail(
        subject=subject,
        from_address=from_address,
        from_name=from_name,
        received_at=received_at,
        body_text=body_text,
        top_links=top_links,
    )


def _parse_from_address(from_header: str) -> tuple[str, str | None]:
    from_header = from_header.strip()
    if "<" in from_header and ">" in from_header:
        parts = from_header.split("<")
        name = parts[0].strip().strip('"') or None
        address = parts[1].rstrip(">").strip()
    else:
        address = from_header
        name = None
    return address.lower(), name


def _get_best_body(msg: EmailMessage) -> tuple[str | None, list[str] | None]:
    text_part = None
    html_part = None

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and text_part is None:
                text_part = part
            elif ctype == "text/html" and html_part is None:
                html_part = part
    else:
        ctype = msg.get_content_type()
        if ctype == "text/plain":
            text_part = msg
        elif ctype == "text/html":
            html_part = msg

    if text_part:
        return text_part.get_content(), None

    if html_part:
        html = html_part.get_content()
        links = extract_top_links(html)
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.body_width = 0
        return converter.handle(html), links if links else None

    return None, None
