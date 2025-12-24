"""Database connection and session management."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from dealintel.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def acquire_advisory_lock(session: Session, lock_name: str) -> bool:
    """Acquire Postgres advisory lock (prevents concurrent runs)."""
    lock_id = hash(lock_name) % (2**31)
    result = session.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id})
    return bool(result.scalar())


def release_advisory_lock(session: Session, lock_name: str) -> None:
    """Release Postgres advisory lock."""
    lock_id = hash(lock_name) % (2**31)
    session.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
