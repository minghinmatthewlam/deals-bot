"""Pytest fixtures for Deal Intelligence tests."""

import os
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from dealintel.models import Base, EmailRaw, Promo, PromoChange, Store, StoreSource

# Test database URL - use separate database
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://dealintel:dealintel_dev@localhost:5432/dealintel_test",
)


@pytest.fixture(scope="session")
def engine():
    """Create test database engine."""
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(engine) -> Generator[Session, None, None]:
    """Provide a transactional scope around each test."""
    connection = engine.connect()
    transaction = connection.begin()

    session_local = sessionmaker(bind=connection)
    session = session_local()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def sample_store(db_session: Session) -> Store:
    """Create a sample store for testing."""
    store = Store(
        slug="test-store",
        name="Test Store",
        website_url="https://teststore.com",
        category="test",
        active=True,
    )
    db_session.add(store)
    db_session.flush()

    # Add source
    source = StoreSource(
        store_id=store.id,
        source_type="gmail_from_domain",
        pattern="teststore.com",
        priority=100,
        active=True,
    )
    db_session.add(source)
    db_session.flush()

    return store


@pytest.fixture
def sample_email(db_session: Session, sample_store: Store) -> EmailRaw:
    """Create a sample email for testing."""
    email = EmailRaw(
        gmail_message_id=f"test-{uuid4().hex[:8]}",
        gmail_thread_id="thread-123",
        store_id=sample_store.id,
        from_address="deals@teststore.com",
        from_domain="teststore.com",
        from_name="Test Store",
        subject="25% Off Everything!",
        received_at=datetime.now(UTC),
        body_text="""
        Test Store - Big Sale!

        Get 25% off everything with code SAVE25.

        Offer ends December 31, 2024.

        Shop now: https://teststore.com/sale
        """,
        body_hash="abc123",
        top_links=["https://teststore.com/sale"],
        extraction_status="pending",
    )
    db_session.add(email)
    db_session.flush()

    return email


@pytest.fixture
def sample_promo(db_session: Session, sample_store: Store, sample_email: EmailRaw) -> Promo:
    """Create a sample promo for testing."""
    now = datetime.now(UTC)
    promo = Promo(
        store_id=sample_store.id,
        base_key="code:SAVE25",
        headline="25% Off Everything",
        discount_text="25% off",
        percent_off=25.0,
        code="SAVE25",
        first_seen_at=now,
        last_seen_at=now,
        status="active",
    )
    db_session.add(promo)
    db_session.flush()

    # Add creation change
    change = PromoChange(
        promo_id=promo.id,
        email_id=sample_email.id,
        change_type="created",
        diff_json={},
        changed_at=now,
    )
    db_session.add(change)
    db_session.flush()

    return promo


@pytest.fixture
def mock_gmail_service():
    """Mock Gmail API service."""
    service = MagicMock()

    # Mock profile
    service.users().getProfile().execute.return_value = {
        "historyId": "12345",
    }

    # Mock messages list
    service.users().messages().list().execute.return_value = {
        "messages": [],
        "nextPageToken": None,
    }

    # Mock history list
    service.users().history().list().execute.return_value = {
        "history": [],
        "historyId": "12346",
    }

    return service


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for extraction."""
    with patch("dealintel.llm.extract.OpenAI") as mock_openai:
        client = MagicMock()
        mock_openai.return_value = client

        # Mock successful extraction response
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    parsed=MagicMock(
                        is_promo_email=True,
                        promos=[
                            MagicMock(
                                headline="25% Off Everything",
                                summary="Sitewide sale",
                                discount_text="25% off",
                                percent_off=25.0,
                                amount_off=None,
                                code="SAVE25",
                                starts_at=None,
                                ends_at="2024-12-31T23:59:59Z",
                                end_inferred=False,
                                exclusions=[],
                                landing_url="https://teststore.com/sale",
                                confidence=0.9,
                                missing_fields=[],
                            )
                        ],
                        notes=[],
                    )
                )
            )
        ]
        client.beta.chat.completions.parse.return_value = mock_response

        yield client


@pytest.fixture
def sample_emails_dir(tmp_path):
    """Create temporary directory with sample email files."""
    emails_dir = tmp_path / "emails"
    emails_dir.mkdir()

    # Create sample email file
    (emails_dir / "nike_promo.eml").write_text(
        """
From: Nike <nike@email.nike.com>
Subject: Just Dropped: 30% Off Select Styles
Date: Mon, 20 Dec 2024 10:00:00 -0500

Nike Members Only!

Get 30% off select styles with code MEMBER30.

Offer ends Sunday at midnight.

SHOP NOW: https://nike.com/sale

Exclusions apply. See details.
        """
    )

    return emails_dir


@pytest.fixture
def golden_dir(tmp_path):
    """Create temporary directory for golden file tests."""
    golden = tmp_path / "golden"
    golden.mkdir()

    # Create expected output
    import json

    (golden / "nike_promo.json").write_text(
        json.dumps(
            {
                "is_promo_email": True,
                "promos": [
                    {
                        "headline": "30% Off Select Styles",
                        "discount_text": "30% off",
                        "percent_off": 30.0,
                        "code": "MEMBER30",
                    }
                ],
            },
            indent=2,
        )
    )

    return golden
