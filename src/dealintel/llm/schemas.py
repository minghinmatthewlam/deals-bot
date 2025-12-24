"""Pydantic schemas for OpenAI structured extraction."""

from pydantic import BaseModel, Field


class FlightDeal(BaseModel):
    """Flight-specific deal information."""

    origins: list[str] = Field(default_factory=list)
    destinations: list[str] = Field(default_factory=list)
    destination_region: str | None = None
    price_usd: float | None = None
    travel_window: str | None = None
    booking_url: str | None = None


class PromoCandidate(BaseModel):
    """A promotional offer extracted from an email."""

    headline: str = Field(..., description="Main promo description (e.g., '25% Off Everything')")
    summary: str | None = Field(None, description="Optional additional context")
    discount_text: str | None = Field(None, description="Human-readable discount (e.g., '25% off')")
    percent_off: float | None = Field(None, ge=0, le=100, description="Numeric percentage discount")
    amount_off: float | None = Field(None, ge=0, description="Dollar amount off")
    code: str | None = Field(None, description="Promo code if any (e.g., 'SAVE25')")
    starts_at: str | None = Field(None, description="Start date in ISO 8601 format")
    ends_at: str | None = Field(None, description="End date in ISO 8601 format")
    end_inferred: bool = Field(False, description="True if end date was inferred from context")
    exclusions: list[str] = Field(default_factory=list, description="Fine print restrictions")
    landing_url: str | None = Field(None, description="URL to shop the promo")
    confidence: float = Field(0.5, ge=0, le=1, description="Extraction confidence (0-1)")
    missing_fields: list[str] = Field(default_factory=list, description="Fields that couldn't be extracted")
    vertical: str = Field("retail", description="retail|flight|other")
    flight: FlightDeal | None = None


class ExtractionResult(BaseModel):
    """Result of extracting promos from an email."""

    is_promo_email: bool = Field(..., description="False for non-promotional emails (newsletters, order confirmations)")
    promos: list[PromoCandidate] = Field(default_factory=list, description="Extracted promotional offers")
    notes: list[str] = Field(default_factory=list, description="LLM observations about the extraction")
