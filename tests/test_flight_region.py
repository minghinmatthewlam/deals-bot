"""Tests for flight region normalization and filtering."""

from dealintel.llm.extract import _filter_flight_promos, _normalize_region
from dealintel.llm.schemas import ExtractionResult, FlightDeal, PromoCandidate
from dealintel.prefs import FlightPrefs, Preferences


def test_normalize_region_variants():
    assert _normalize_region("Western Europe") == "europe"
    assert _normalize_region("SOUTH EAST ASIA") == "asia"


def test_filter_allows_normalized_region(monkeypatch):
    prefs = Preferences(
        flights=FlightPrefs(
            origins=["SFO"],
            destination_regions=["Europe"],
            max_price_usd={"Europe": 600},
        )
    )
    monkeypatch.setattr("dealintel.llm.extract.load_preferences", lambda: prefs)

    promo = PromoCandidate(
        headline="SFO to Paris",
        vertical="flight",
        flight=FlightDeal(
            origins=["SFO"],
            destinations=["CDG"],
            destination_region="Western Europe",
            price_usd=500,
        ),
    )
    result = ExtractionResult(is_promo_email=True, promos=[promo])

    filtered = _filter_flight_promos(result)
    assert len(filtered.promos) == 1


def test_filter_blocks_mismatched_region(monkeypatch):
    prefs = Preferences(
        flights=FlightPrefs(
            origins=["SFO"],
            destination_regions=["Europe"],
            max_price_usd={"Europe": 600},
        )
    )
    monkeypatch.setattr("dealintel.llm.extract.load_preferences", lambda: prefs)

    promo = PromoCandidate(
        headline="SFO to Lima",
        vertical="flight",
        flight=FlightDeal(
            origins=["SFO"],
            destinations=["LIM"],
            destination_region="South America",
            price_usd=500,
        ),
    )
    result = ExtractionResult(is_promo_email=True, promos=[promo])

    filtered = _filter_flight_promos(result)
    assert len(filtered.promos) == 0
