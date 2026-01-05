"""Unit tests for digest selection and rendering."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


class TestSelectDigestPromos:
    """Tests for select_digest_promos()."""

    def test_selects_new_promos(self, db_session, sample_promo):
        """Should select newly created promos."""
        from dealintel.digest.select import select_digest_promos

        # Promo was just created, should be selected
        with patch("dealintel.digest.select.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: db_session
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            with (
                patch("dealintel.digest.select.get_last_digest_time") as mock_time,
                patch("dealintel.digest.select.get_store_allowlist") as mock_allowlist,
            ):
                # Last digest was 1 hour ago
                mock_time.return_value = datetime.now(UTC) - timedelta(hours=1)
                mock_allowlist.return_value = set()

                promos = select_digest_promos()

                # Should have our sample promo
                assert len(promos) >= 1
                badges = [p["badge"] for p in promos]
                assert "NEW" in badges

    def test_excludes_old_promos(self, db_session, sample_promo, sample_email):
        """Should not select promos created before last digest."""
        from dealintel.digest.select import select_digest_promos
        from dealintel.models import PromoChange

        # Update the promo's creation to be old
        change = db_session.query(PromoChange).filter_by(promo_id=sample_promo.id, change_type="created").first()
        change.changed_at = datetime.now(UTC) - timedelta(days=2)
        db_session.flush()

        with patch("dealintel.digest.select.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__ = lambda s: db_session
            mock_get_db.return_value.__exit__ = MagicMock(return_value=False)

            with (
                patch("dealintel.digest.select.get_last_digest_time") as mock_time,
                patch("dealintel.digest.select.get_store_allowlist") as mock_allowlist,
            ):
                # Last digest was 1 day ago
                mock_time.return_value = datetime.now(UTC) - timedelta(days=1)
                mock_allowlist.return_value = set()

                promos = select_digest_promos()

                # Should not have our old promo
                promo_ids = [p["promo"].id for p in promos]
                assert sample_promo.id not in promo_ids


class TestGroupByStore:
    """Tests for group_by_store()."""

    def test_groups_correctly(self):
        """Should group promos by store name."""
        from dealintel.digest.render import group_by_store

        promos = [
            {"store_name": "Nike", "promo": MagicMock()},
            {"store_name": "REI", "promo": MagicMock()},
            {"store_name": "Nike", "promo": MagicMock()},
        ]

        grouped = group_by_store(promos)

        assert "Nike" in grouped
        assert "REI" in grouped
        assert len(grouped["Nike"]) == 2
        assert len(grouped["REI"]) == 1

    def test_sorts_alphabetically(self):
        """Should sort stores alphabetically."""
        from dealintel.digest.render import group_by_store

        promos = [
            {"store_name": "Zebra", "promo": MagicMock()},
            {"store_name": "Apple", "promo": MagicMock()},
            {"store_name": "Nike", "promo": MagicMock()},
        ]

        grouped = group_by_store(promos)

        assert list(grouped.keys()) == ["Apple", "Nike", "Zebra"]
