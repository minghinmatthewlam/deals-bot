"""Tests for inbound .eml ingestion."""

from unittest.mock import MagicMock, patch

from dealintel.inbound.ingest import ingest_inbound_eml_dir
from dealintel.inbound.parse_eml import parse_eml
from dealintel.models import EmailRaw


class TestParseEml:
    def test_parses_basic_headers(self):
        raw = b"""From: Test Sender <test@example.com>\nSubject: Promo\nDate: Mon, 20 Dec 2024 10:00:00 -0500\n\nHello world\n"""
        parsed = parse_eml(raw)

        assert parsed.subject == "Promo"
        assert parsed.from_address == "test@example.com"
        assert parsed.from_name == "Test Sender"
        assert parsed.body_text is not None
        assert "Hello world" in parsed.body_text


class TestInboundIngest:
    def test_ingest_eml_files(self, db_session, sample_emails_dir):
        with patch("dealintel.inbound.ingest.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: db_session
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            stats = ingest_inbound_eml_dir(str(sample_emails_dir))

            assert stats["files"] == 1
            assert stats["new"] == 1
            assert stats["matched"] == 0
            assert stats["unmatched"] == 1
            assert db_session.query(EmailRaw).count() == 1
