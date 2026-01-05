"""OpenAI extraction using structured outputs."""

import re

import structlog
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from dealintel.config import settings
from dealintel.llm.schemas import ExtractionResult
from dealintel.models import EmailRaw
from dealintel.prefs import load_preferences
from dealintel.storage.payloads import get_email_body

logger = structlog.get_logger()

SYSTEM_PROMPT = """You are an expert at extracting promotional offers from marketing emails.

Your task is to analyze email content and extract all promotional offers present.

Guidelines:
1. Set is_promo_email=false for:
   - Newsletters with no specific deals
   - Order confirmations/shipping notifications
   - Account updates or password resets
   - Surveys or feedback requests

2. For promotional emails:
   - Extract ALL distinct offers (some emails have multiple)
   - Only extract savings-focused offers (percent off, dollar off, sale/clearance, promo code)
   - Exclude product launches, restocks, content-only announcements, and generic product links
   - Free shipping alone is NOT a deal
   - For flight deals, require an explicit price (e.g., "$299 round-trip")
   - Parse dates carefully (handle "ends Sunday", "this weekend only", "limited time")
   - If end date is not explicit but can be inferred, set end_inferred=true
   - Extract promo codes EXACTLY as shown (case-sensitive)
   - Note ambiguity in the notes[] field

3. Confidence scoring:
   - 0.8+ for clear, explicit promos with dates and codes
   - 0.5-0.8 for promos with some missing details
   - <0.5 for ambiguous or unclear offers

4. Landing URL:
   - Extract the most relevant "shop now" or promo-specific link
   - Prefer clean URLs over tracking-heavy ones when possible

5. Vertical classification:
   - Use promo.vertical="flight" for airfare deals and populate promo.flight
   - Use promo.vertical="retail" for typical shopping promos
   - Use promo.vertical="other" for non-retail, non-flight promos

Be thorough but accurate. It's better to miss an ambiguous promo than to extract false positives.
"""


def format_email_for_extraction(email: EmailRaw) -> str:
    """Format email content for LLM extraction."""
    parts = []

    if email.store:
        parts.append(f"Store: {email.store.name}")

    parts.append(f"Subject: {email.subject}")
    parts.append(f"Date: {email.received_at.strftime('%Y-%m-%d %H:%M')}")
    parts.append("")

    # Truncate body to ~3000 chars to stay within token budget
    body = get_email_body(email.body_text, email.payload_ref)
    if len(body) > 3000:
        body = body[:3000] + "\n\n[TRUNCATED]"
    parts.append(body)

    if email.top_links:
        parts.append("\nTop Links:")
        for link in email.top_links[:5]:
            parts.append(f"- {link}")

    return "\n".join(parts)


def _filter_flight_promos(result: ExtractionResult) -> ExtractionResult:
    """Filter flight promos against preferences, keeping non-flight promos untouched."""
    prefs = load_preferences()
    preferred_origins = {origin.strip().upper() for origin in prefs.flights.origins if origin}
    preferred_regions = {_normalize_region(region) for region in prefs.flights.destination_regions if region}
    max_price_by_region = {_normalize_region(region): price for region, price in prefs.flights.max_price_usd.items()}

    filtered = []
    for promo in result.promos:
        flight = promo.flight
        is_flight = promo.vertical == "flight" or flight is not None

        if not is_flight or flight is None:
            filtered.append(promo)
            continue

        if flight.price_usd is None:
            continue

        if preferred_origins and flight.origins:
            flight_origins = {origin.strip().upper() for origin in flight.origins if origin}
            if flight_origins.isdisjoint(preferred_origins):
                continue

        normalized_region = _normalize_region(flight.destination_region) if flight.destination_region else ""

        if preferred_regions and normalized_region and normalized_region not in preferred_regions:
            continue

        if flight.price_usd is not None and normalized_region:
            max_price = max_price_by_region.get(normalized_region)
            if max_price is not None and flight.price_usd > max_price:
                continue

        filtered.append(promo)

    if len(filtered) != len(result.promos):
        logger.info(
            "Filtered flight promos by preferences",
            before=len(result.promos),
            after=len(filtered),
        )

    return result.model_copy(update={"promos": filtered})


def _normalize_region(value: str) -> str:
    if not value:
        return ""

    normalized = re.sub(r"\\s+", " ", value.strip().lower())
    if "europe" in normalized:
        return "europe"
    if "asia" in normalized:
        return "asia"
    if "north america" in normalized:
        return "north america"
    if "south america" in normalized or "latin america" in normalized:
        return "south america"
    if "middle east" in normalized:
        return "middle east"
    if "africa" in normalized:
        return "africa"
    if "oceania" in normalized or "australia" in normalized or "new zealand" in normalized:
        return "oceania"
    return normalized


_FREE_SHIPPING_PATTERN = re.compile(r"\bfree\s+shipping\b", re.IGNORECASE)
_NUMERIC_DISCOUNT_PATTERN = re.compile(
    r"(\$\s?\d+(?:\.\d+)?|\b\d{1,3}\s?%\s*off\b|\bsave\s+\$?\d+)",
    re.IGNORECASE,
)
_SAVINGS_KEYWORDS = (
    "sale",
    "clearance",
    "markdown",
    "bogo",
    "buy one get one",
    "2 for 1",
    "half off",
)


def _has_savings_signal(text: str) -> bool:
    if not text:
        return False
    if _NUMERIC_DISCOUNT_PATTERN.search(text):
        return True
    lowered = text.lower()
    return any(keyword in lowered for keyword in _SAVINGS_KEYWORDS)


def _filter_non_discount_promos(result: ExtractionResult) -> ExtractionResult:
    """Filter out promos that do not clearly save the user money."""
    if not result.promos:
        if result.is_promo_email:
            return result.model_copy(update={"is_promo_email": False})
        return result

    filtered = []
    for promo in result.promos:
        if promo.vertical == "flight" and promo.flight and promo.flight.price_usd is not None:
            filtered.append(promo)
            continue
        if promo.percent_off and promo.percent_off > 0:
            filtered.append(promo)
            continue
        if promo.amount_off and promo.amount_off > 0:
            filtered.append(promo)
            continue
        if promo.code:
            filtered.append(promo)
            continue

        combined_text = " ".join(text for text in (promo.discount_text, promo.headline, promo.summary) if text).strip()
        if not combined_text:
            continue

        if _FREE_SHIPPING_PATTERN.search(combined_text) and not _has_savings_signal(
            _FREE_SHIPPING_PATTERN.sub("", combined_text)
        ):
            continue

        if _has_savings_signal(combined_text):
            filtered.append(promo)

    if len(filtered) == len(result.promos):
        return result

    updated_notes = list(result.notes)
    updated_notes.append("Filtered non-discount promos")
    return result.model_copy(
        update={
            "promos": filtered,
            "is_promo_email": result.is_promo_email and bool(filtered),
            "notes": updated_notes,
        }
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def extract_promos(email: EmailRaw) -> ExtractionResult:
    """Extract promos using OpenAI structured outputs (guaranteed schema compliance)."""
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())

    response = client.beta.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": format_email_for_extraction(email)},
        ],
        temperature=0.1,  # Low for consistency
        response_format=ExtractionResult,
    )

    result = response.choices[0].message.parsed
    if result is None:
        raise RuntimeError("OpenAI response missing parsed extraction result")
    result = _filter_flight_promos(result)
    result = _filter_non_discount_promos(result)
    logger.info(
        "Extraction complete",
        email_id=str(email.id),
        is_promo=result.is_promo_email,
        promo_count=len(result.promos),
    )
    return result
