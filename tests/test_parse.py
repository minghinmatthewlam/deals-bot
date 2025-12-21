"""Unit tests for email parsing functions."""

import pytest

from dealintel.gmail.parse import compute_body_hash, extract_top_links, parse_from_address


class TestParseFromAddress:
    """Tests for parse_from_address()."""

    def test_simple_email(self):
        """Should parse simple email address."""
        email, name = parse_from_address("test@example.com")
        assert email == "test@example.com"
        assert name is None

    def test_email_with_name(self):
        """Should parse email with display name."""
        email, name = parse_from_address("Test User <test@example.com>")
        assert email == "test@example.com"
        assert name == "Test User"

    def test_email_with_quoted_name(self):
        """Should parse email with quoted display name."""
        email, name = parse_from_address('"Test User" <test@example.com>')
        assert email == "test@example.com"
        assert name == "Test User"

    def test_lowercases_email(self):
        """Should lowercase email address."""
        email, name = parse_from_address("Test@Example.COM")
        assert email == "test@example.com"

    def test_empty_string(self):
        """Should handle empty string."""
        email, name = parse_from_address("")
        assert email == ""
        assert name is None


class TestExtractTopLinks:
    """Tests for extract_top_links()."""

    def test_extracts_links(self):
        """Should extract href links from HTML."""
        html = """
        <html>
        <body>
            <a href="https://example.com/link1">Link 1</a>
            <a href="https://example.com/link2">Link 2</a>
        </body>
        </html>
        """
        links = extract_top_links(html)
        assert links == ["https://example.com/link1", "https://example.com/link2"]

    def test_limits_count(self):
        """Should limit to specified number of links."""
        html = """
        <html><body>
            <a href="https://example.com/1">1</a>
            <a href="https://example.com/2">2</a>
            <a href="https://example.com/3">3</a>
            <a href="https://example.com/4">4</a>
            <a href="https://example.com/5">5</a>
        </body></html>
        """
        links = extract_top_links(html, limit=3)
        assert len(links) == 3

    def test_skips_mailto(self):
        """Should skip mailto links."""
        html = '<a href="mailto:test@example.com">Email</a>'
        links = extract_top_links(html)
        assert links == []

    def test_skips_tel(self):
        """Should skip tel links."""
        html = '<a href="tel:+1234567890">Call</a>'
        links = extract_top_links(html)
        assert links == []

    def test_skips_javascript(self):
        """Should skip javascript links."""
        html = '<a href="javascript:void(0)">Click</a>'
        links = extract_top_links(html)
        assert links == []

    def test_skips_anchors(self):
        """Should skip anchor-only links."""
        html = '<a href="#section">Jump</a>'
        links = extract_top_links(html)
        assert links == []

    def test_deduplicates(self):
        """Should deduplicate links."""
        html = """
        <a href="https://example.com">Link</a>
        <a href="https://example.com">Same Link</a>
        """
        links = extract_top_links(html)
        assert links == ["https://example.com"]


class TestComputeBodyHash:
    """Tests for compute_body_hash()."""

    def test_produces_hash(self):
        """Should produce a hex hash."""
        hash_val = compute_body_hash("test content")
        assert len(hash_val) == 64  # SHA256 hex

    def test_consistent_for_same_input(self):
        """Same input should produce same hash."""
        hash1 = compute_body_hash("test content")
        hash2 = compute_body_hash("test content")
        assert hash1 == hash2

    def test_case_insensitive(self):
        """Should be case-insensitive."""
        hash1 = compute_body_hash("TEST CONTENT")
        hash2 = compute_body_hash("test content")
        assert hash1 == hash2

    def test_whitespace_normalized(self):
        """Should normalize whitespace."""
        hash1 = compute_body_hash("test   content")
        hash2 = compute_body_hash("test content")
        assert hash1 == hash2
