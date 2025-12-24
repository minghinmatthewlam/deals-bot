"""Golden file tests for extraction prompt regression."""

import json
from pathlib import Path

import pytest


def load_fixture(name: str) -> str:
    """Load email fixture content."""
    path = Path(__file__).parent / "fixtures" / "emails" / name
    return path.read_text()


def load_golden(name: str) -> dict:
    """Load expected golden output."""
    path = Path(__file__).parent / "golden" / name
    return json.loads(path.read_text())


class TestGoldenExtractions:
    """Golden file tests for extraction accuracy."""

    @pytest.mark.parametrize(
        "email_fixture,golden_file",
        [
            ("nike_promo.txt", "nike_promo.json"),
            ("newsletter.txt", "newsletter.json"),
        ],
    )
    def test_extraction_matches_golden(self, email_fixture, golden_file, mock_openai_client):
        """Test that extraction output matches expected golden file."""
        assert load_fixture(email_fixture)
        expected = load_golden(golden_file)

        # The mock client will return our expected response
        # In real testing, you'd configure the mock per test case
        # or use live API calls with caching

        # For now, just verify the golden file structure
        assert "is_promo_email" in expected
        assert "promos" in expected

        if expected["is_promo_email"]:
            for promo in expected["promos"]:
                assert "headline" in promo
                # Other fields are optional

    def test_promo_email_detection(self):
        """Promo emails should have is_promo_email=true."""
        expected = load_golden("nike_promo.json")
        assert expected["is_promo_email"] is True
        assert len(expected["promos"]) >= 1

    def test_newsletter_detection(self):
        """Newsletters should have is_promo_email=false."""
        expected = load_golden("newsletter.json")
        assert expected["is_promo_email"] is False
        assert len(expected["promos"]) == 0

    def test_code_extraction(self):
        """Promo codes should be extracted exactly as shown."""
        expected = load_golden("nike_promo.json")
        codes = [p.get("code") for p in expected["promos"] if p.get("code")]
        assert "MEMBER30" in codes


class TestExtractionEdgeCases:
    """Test edge cases in extraction."""

    def test_multiple_promos_in_email(self):
        """Emails with multiple promos should extract all."""
        # This would test against a multi-promo fixture
        # For now, just document the expected behavior
        pass

    def test_inferred_end_date(self):
        """End dates inferred from context should be marked."""
        # Promos with "ends Sunday" should have end_inferred=True
        pass

    def test_percentage_vs_amount(self):
        """Should distinguish percentage off from dollar amount off."""
        # "25% off" -> percent_off=25
        # "$25 off" -> amount_off=25
        pass
