"""OpenAI extraction using structured outputs."""

import structlog
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from dealintel.config import settings
from dealintel.llm.schemas import ExtractionResult
from dealintel.models import EmailRaw
from dealintel.prefs import load_preferences

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
    body = email.body_text or ""
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
    preferred_regions = {region.strip().lower() for region in prefs.flights.destination_regions if region}
    max_price_by_region = {region.lower(): price for region, price in prefs.flights.max_price_usd.items()}

    filtered = []
    for promo in result.promos:
        flight = promo.flight
        is_flight = promo.vertical == "flight" or flight is not None

        if not is_flight or flight is None:
            filtered.append(promo)
            continue

        if preferred_origins and flight.origins:
            flight_origins = {origin.strip().upper() for origin in flight.origins if origin}
            if flight_origins.isdisjoint(preferred_origins):
                continue

        if preferred_regions and flight.destination_region:
            if flight.destination_region.strip().lower() not in preferred_regions:
                continue

        if flight.price_usd is not None and flight.destination_region:
            region_key = flight.destination_region.strip().lower()
            max_price = max_price_by_region.get(region_key)
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
    logger.info(
        "Extraction complete",
        email_id=str(email.id),
        is_promo=result.is_promo_email,
        promo_count=len(result.promos),
    )
    return result
