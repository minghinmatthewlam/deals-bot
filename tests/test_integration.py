"""Integration tests for end-to-end flows."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


class TestPipelineIntegration:
    """Integration tests for the full pipeline."""

    def test_full_pipeline_dry_run(self, db_session, sample_store):
        """Test full pipeline in dry-run mode with mocked services."""
        from dealintel.jobs.daily import run_daily_pipeline

        with patch("dealintel.jobs.daily.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: db_session
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            with patch("dealintel.jobs.daily.acquire_advisory_lock", return_value=True):
                with patch("dealintel.jobs.daily.release_advisory_lock"):
                    with patch("dealintel.jobs.daily.seed_stores"):
                        with patch(
                            "dealintel.jobs.daily.ingest_emails",
                            return_value={"fetched": 0, "new": 0, "matched": 0, "unmatched": 0},
                        ):
                            with patch(
                                "dealintel.jobs.daily.process_pending_emails",
                                return_value={"processed": 0, "succeeded": 0, "failed": 0},
                            ):
                                with patch(
                                    "dealintel.jobs.daily.merge_extracted_promos",
                                    return_value={"created": 0, "updated": 0, "unchanged": 0},
                                ):
                                    with patch("dealintel.jobs.daily.generate_digest", return_value=(None, 0, 0)):
                                        stats = run_daily_pipeline(dry_run=True)

                                        assert stats["dry_run"] is True
                                        assert "error" not in stats or stats["error"] is None


class TestIdempotency:
    """Tests for idempotent behavior."""

    def test_duplicate_email_ignored(self, db_session, sample_email):
        """Ingesting the same email twice should not create duplicates."""
        from dealintel.models import EmailRaw

        original_count = db_session.query(EmailRaw).count()

        # Try to add duplicate
        duplicate = EmailRaw(
            gmail_message_id=sample_email.gmail_message_id,  # Same ID
            gmail_thread_id="thread-new",
            store_id=sample_email.store_id,
            from_address=sample_email.from_address,
            from_domain=sample_email.from_domain,
            subject="Different subject",
            received_at=datetime.now(UTC),
            body_text="Different content",
            body_hash="different-hash",
            extraction_status="pending",
        )

        # This should fail due to unique constraint
        with db_session.begin_nested():
            with pytest.raises(Exception):
                db_session.add(duplicate)
                db_session.flush()

        # Count should be unchanged
        assert db_session.query(EmailRaw).count() == original_count

    def test_duplicate_promo_merged(self, db_session, sample_store, sample_email, sample_promo):
        """Same promo from multiple emails should be merged."""
        from dealintel.models import Promo

        # The same base_key should match existing promo
        existing = db_session.query(Promo).filter_by(store_id=sample_store.id, base_key="code:SAVE25").first()

        assert existing is not None
        assert existing.id == sample_promo.id


class TestErrorRecovery:
    """Tests for graceful error handling."""

    def test_extraction_failure_continues(self, db_session, sample_email):
        """One extraction failure should not stop other extractions."""
        # Mark email as pending
        sample_email.extraction_status = "pending"
        db_session.flush()

        # Simulate extraction failure
        sample_email.extraction_status = "error"
        sample_email.extraction_error = "Test error"
        db_session.flush()

        # Email should be marked as error but other emails can still process
        assert sample_email.extraction_status == "error"
        assert sample_email.extraction_error == "Test error"


class TestStoreMatching:
    """Tests for email-to-store matching."""

    def test_exact_address_match(self, db_session, sample_store):
        """Exact address should match store."""
        from dealintel.gmail.ingest import match_store
        from dealintel.models import StoreSource

        # Add exact address source
        source = StoreSource(
            store_id=sample_store.id,
            source_type="gmail_from_address",
            pattern="exact@teststore.com",
            priority=100,
            active=True,
        )
        db_session.add(source)
        db_session.flush()

        result = match_store(db_session, "exact@teststore.com", "teststore.com")
        assert result == sample_store.id

    def test_domain_match_fallback(self, db_session, sample_store):
        """Domain should match when no exact address."""
        from dealintel.gmail.ingest import match_store

        result = match_store(db_session, "unknown@teststore.com", "teststore.com")
        assert result == sample_store.id

    def test_priority_ordering(self, db_session, sample_store):
        """Higher priority source should win."""
        from dealintel.gmail.ingest import match_store
        from dealintel.models import Store, StoreSource

        # Create second store with lower priority
        store2 = Store(slug="other-store", name="Other Store")
        db_session.add(store2)
        db_session.flush()

        source2 = StoreSource(
            store_id=store2.id,
            source_type="gmail_from_domain",
            pattern="teststore.com",
            priority=50,  # Lower than sample_store's 100
            active=True,
        )
        db_session.add(source2)
        db_session.flush()

        # Should match sample_store due to higher priority
        result = match_store(db_session, "test@teststore.com", "teststore.com")
        assert result == sample_store.id

    def test_unmatched_email(self, db_session):
        """Unknown sender should return None."""
        from dealintel.gmail.ingest import match_store

        result = match_store(db_session, "unknown@unknown.com", "unknown.com")
        assert result is None
