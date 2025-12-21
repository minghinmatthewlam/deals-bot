"""Promo normalization utilities."""

import hashlib
import re
from urllib.parse import urlparse


def normalize_url(url: str) -> str | None:
    """Remove query params and fragments for stable URL comparison.

    Example:
        Input:  https://nike.com/sale?utm_source=email#top
        Output: nike.com/sale
    """
    if not url:
        return None

    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return None

        # Lowercase host, keep path, strip trailing slash
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        return f"{host}{path}" if path else host
    except Exception:
        return None


def normalize_headline(headline: str) -> str:
    """Normalize headline for stable comparison.

    Steps:
    1. Lowercase
    2. Remove extra whitespace
    3. Strip punctuation (optional)
    """
    if not headline:
        return ""

    # Lowercase and collapse whitespace
    normalized = re.sub(r"\s+", " ", headline.lower().strip())

    # Remove punctuation for more fuzzy matching
    normalized = re.sub(r"[^\w\s]", "", normalized)

    return normalized


def compute_base_key(code: str | None, landing_url: str | None, headline: str) -> str:
    """Compute stable dedup key with priority hierarchy.

    Priority:
    1. Code (most stable - promo codes are globally unique)
    2. URL path (stable across email variations)
    3. Headline hash (fallback for codeless promos)
    """
    # 1. Code takes priority
    if code:
        return f"code:{code.upper().strip()}"

    # 2. Normalized URL
    if landing_url:
        normalized = normalize_url(landing_url)
        if normalized:
            return f"url:{normalized}"

    # 3. Headline hash fallback
    headline_hash = hashlib.md5(normalize_headline(headline).encode()).hexdigest()[:16]
    return f"head:{headline_hash}"
