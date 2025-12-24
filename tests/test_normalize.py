"""Unit tests for promo normalization functions."""

from dealintel.promos.normalize import compute_base_key, normalize_headline, normalize_url


class TestNormalizeUrl:
    """Tests for normalize_url()."""

    def test_removes_query_params(self):
        """Should remove query parameters."""
        url = "https://nike.com/sale?utm_source=email&utm_medium=promo"
        assert normalize_url(url) == "nike.com/sale"

    def test_removes_fragment(self):
        """Should remove URL fragments."""
        url = "https://nike.com/sale#top"
        assert normalize_url(url) == "nike.com/sale"

    def test_lowercases_host(self):
        """Should lowercase the host."""
        url = "https://Nike.COM/Sale"
        assert normalize_url(url) == "nike.com/Sale"

    def test_strips_trailing_slash(self):
        """Should strip trailing slashes."""
        url = "https://nike.com/sale/"
        assert normalize_url(url) == "nike.com/sale"

    def test_handles_root_url(self):
        """Should handle URLs with no path."""
        url = "https://nike.com"
        assert normalize_url(url) == "nike.com"

    def test_returns_none_for_empty(self):
        """Should return None for empty URLs."""
        assert normalize_url("") is None
        assert normalize_url(None) is None

    def test_returns_none_for_invalid(self):
        """Should return None for invalid URLs."""
        assert normalize_url("not-a-url") is None


class TestNormalizeHeadline:
    """Tests for normalize_headline()."""

    def test_lowercases(self):
        """Should lowercase the headline."""
        assert normalize_headline("25% OFF") == "25 off"

    def test_collapses_whitespace(self):
        """Should collapse multiple whitespace to single space."""
        assert normalize_headline("25%   OFF   Everything") == "25 off everything"

    def test_strips_leading_trailing(self):
        """Should strip leading and trailing whitespace."""
        assert normalize_headline("  25% OFF  ") == "25 off"

    def test_removes_punctuation(self):
        """Should remove punctuation."""
        assert normalize_headline("25% OFF!!! Everything!!!") == "25 off everything"

    def test_handles_empty(self):
        """Should handle empty strings."""
        assert normalize_headline("") == ""
        assert normalize_headline("   ") == ""


class TestComputeBaseKey:
    """Tests for compute_base_key()."""

    def test_code_takes_priority(self):
        """Code should take priority over URL and headline."""
        key = compute_base_key(
            code="SAVE25",
            landing_url="https://nike.com/sale",
            headline="25% Off Everything",
        )
        assert key == "code:SAVE25"

    def test_code_uppercased_and_stripped(self):
        """Code should be uppercased and stripped."""
        key = compute_base_key(
            code="  save25  ",
            landing_url=None,
            headline="25% Off",
        )
        assert key == "code:SAVE25"

    def test_url_second_priority(self):
        """URL should be second priority when no code."""
        key = compute_base_key(
            code=None,
            landing_url="https://nike.com/sale?utm=test",
            headline="25% Off Everything",
        )
        assert key == "url:nike.com/sale"

    def test_headline_fallback(self):
        """Headline hash should be fallback when no code or URL."""
        key = compute_base_key(
            code=None,
            landing_url=None,
            headline="25% Off Everything",
        )
        assert key.startswith("head:")
        assert len(key) == 21  # "head:" + 16 char hash

    def test_headline_hash_stable(self):
        """Same headline should produce same hash."""
        key1 = compute_base_key(None, None, "25% Off Everything")
        key2 = compute_base_key(None, None, "25% Off Everything")
        assert key1 == key2

    def test_headline_hash_case_insensitive(self):
        """Headline hash should be case-insensitive."""
        key1 = compute_base_key(None, None, "25% OFF EVERYTHING")
        key2 = compute_base_key(None, None, "25% off everything")
        assert key1 == key2
