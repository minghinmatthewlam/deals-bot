"""Tests for savings-only promo filtering."""

from dealintel.llm.extract import _filter_flight_promos, _filter_non_discount_promos
from dealintel.llm.schemas import ExtractionResult, FlightDeal, PromoCandidate


def _make_result(promos: list[PromoCandidate]) -> ExtractionResult:
    return ExtractionResult(is_promo_email=True, promos=promos, notes=[])


def test_filters_non_discount_promos():
    result = _make_result([PromoCandidate(headline="New arrivals")])
    filtered = _filter_non_discount_promos(result)
    assert filtered.promos == []
    assert filtered.is_promo_email is False


def test_keeps_sale_keyword():
    result = _make_result([PromoCandidate(headline="Winter Sale")])
    filtered = _filter_non_discount_promos(result)
    assert len(filtered.promos) == 1
    assert filtered.is_promo_email is True


def test_keeps_code_only():
    result = _make_result([PromoCandidate(headline="Use this at checkout", code="SAVE20")])
    filtered = _filter_non_discount_promos(result)
    assert len(filtered.promos) == 1
    assert filtered.promos[0].code == "SAVE20"


def test_filters_free_shipping_only():
    result = _make_result(
        [
            PromoCandidate(
                headline="Free shipping on all orders",
                discount_text="Free shipping",
            )
        ]
    )
    filtered = _filter_non_discount_promos(result)
    assert filtered.promos == []


def test_flight_requires_price():
    promo = PromoCandidate(headline="Flights to Paris", vertical="flight", flight=FlightDeal())
    filtered = _filter_non_discount_promos(_filter_flight_promos(_make_result([promo])))
    assert filtered.promos == []
    assert filtered.is_promo_email is False


def test_flight_with_price_kept():
    promo = PromoCandidate(
        headline="Flights to Paris",
        vertical="flight",
        flight=FlightDeal(price_usd=299.0, destination_region="Europe"),
    )
    filtered = _filter_non_discount_promos(_filter_flight_promos(_make_result([promo])))
    assert len(filtered.promos) == 1
    assert filtered.promos[0].flight is not None
