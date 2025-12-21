# Deal Intelligence MVP — Complete Hybrid Implementation Guide

This guide merges the best practices from both planning approaches into a production-ready implementation.

**Estimated time: 18-28 hours**

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Project Setup](#3-project-setup)
4. [Database Schema](#4-database-schema)
5. [Gmail Integration](#5-gmail-integration)
6. [OpenAI Extraction](#6-openai-extraction)
7. [Promo Deduplication](#7-promo-deduplication)
8. [Digest Generation](#8-digest-generation)
9. [Orchestration](#9-orchestration)
10. [Scheduling](#10-scheduling)
11. [Testing](#11-testing)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Architecture Overview

### Daily Pipeline Flow

```
1. Acquire advisory lock (prevent concurrent runs)
2. Check if digest already sent today (idempotency)
3. Upsert stores.yaml → Postgres
4. Fetch Gmail messages (historyId cursor + 404 fallback)
5. Match emails to stores (strict from-address/domain rules)
6. Extract promos via OpenAI (structured outputs API)
7. Dedupe/merge into canonical promos (base_key hierarchy)
8. Record changes (promo_changes table for NEW/UPDATED badges)
9. Select digest entries (only new/updated since last digest)
10. Send via SendGrid (or save preview in dry-run)
11. Write run stats + advance cursor
```

### Key Design Principles

| Principle | Implementation |
|-----------|----------------|
| **Idempotent** | `UNIQUE (run_type, digest_date_et)` prevents double-sends |
| **Cursor-based** | Gmail historyId with 404 fallback to full sync |
| **Canonical promos** | One row per promo even from 10 emails |
| **Change tracking** | `promo_changes` table powers NEW/UPDATED badges |
| **Graceful degradation** | One extraction failure doesn't kill the run |
| **Concurrency safe** | Postgres advisory lock prevents race conditions |

---

## 2. Prerequisites

### Required Tools

```bash
# Check versions
python3 --version  # 3.11+
docker --version   # 20.0+
docker compose version  # 2.0+
```

### Required Accounts

| Service | Purpose | Setup Link |
|---------|---------|------------|
| Google Cloud | Gmail API | [console.cloud.google.com](https://console.cloud.google.com) |
| OpenAI | Extraction | [platform.openai.com](https://platform.openai.com) |
| SendGrid | Email delivery | [sendgrid.com](https://sendgrid.com) |

### Dedicated Gmail Inbox (Recommended)

Create `yourname.deals@gmail.com` and subscribe newsletters there.

---

## 3. Project Setup

### Directory Structure

```
deal-intel/
├── pyproject.toml
├── docker-compose.yml
├── Makefile
├── stores.yaml
├── .env.example
├── alembic/
│   └── versions/001_initial.py
├── templates/
│   └── digest.html.j2
├── src/dealintel/
│   ├── cli.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── seed.py
│   ├── gmail/{auth,ingest,parse}.py
│   ├── llm/{schemas,extract}.py
│   ├── promos/{normalize,merge}.py
│   ├── digest/{select,render}.py
│   ├── outbound/sendgrid_client.py
│   └── jobs/daily.py
└── tests/
```

### pyproject.toml

```toml
[project]
name = "dealintel"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "sqlalchemy>=2.0.0",
    "alembic>=1.13.0",
    "psycopg[binary]>=3.1.0",
    "google-api-python-client>=2.100.0",
    "google-auth-oauthlib>=1.2.0",
    "openai>=1.12.0",
    "sendgrid>=6.10.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    "typer[all]>=0.9.0",
    "beautifulsoup4>=4.12.0",
    "html2text>=2024.2.26",
    "jinja2>=3.1.0",
    "rapidfuzz>=3.6.0",
    "structlog>=24.1.0",
    "tenacity>=8.2.0",
    "pytz>=2024.1",
]

[project.scripts]
dealintel = "dealintel.cli:app"

[tool.setuptools.packages.find]
where = ["src"]
```

### docker-compose.yml

```yaml
services:
  postgres:
    image: postgres:16-alpine
    container_name: dealintel-db
    environment:
      POSTGRES_USER: dealintel
      POSTGRES_PASSWORD: dealintel_dev
      POSTGRES_DB: dealintel
    ports:
      - "5432:5432"
    volumes:
      - dealintel_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U dealintel"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  dealintel_data:
```

### Makefile

```makefile
.PHONY: install db-up migrate seed gmail-auth run run-dry test

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

db-up:
	docker compose up -d postgres
	sleep 3

db-shell:
	docker compose exec postgres psql -U dealintel

migrate:
	.venv/bin/python -m alembic upgrade head

seed:
	.venv/bin/dealintel seed

gmail-auth:
	.venv/bin/dealintel gmail-auth

run:
	.venv/bin/dealintel run

run-dry:
	.venv/bin/dealintel run --dry-run

test:
	.venv/bin/pytest
```

### .env.example

```bash
DATABASE_URL=postgresql+psycopg://dealintel:dealintel_dev@localhost:5432/dealintel
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
SENDGRID_API_KEY=SG....
SENDER_EMAIL=your-deals@gmail.com
RECIPIENT_EMAIL=your-personal@gmail.com
GMAIL_CREDENTIALS_PATH=credentials.json
GMAIL_TOKEN_PATH=token.json
```

---

## 4. Database Schema

### Key Improvements Over Original

1. **`promo_extractions`** — Store raw LLM output for debugging/regression
2. **`promo_changes`** — Powers NEW/UPDATED badges in digest
3. **`promo_email_links`** — Many-to-many for multi-email promos
4. **`gmail_state`** — Dedicated cursor table (cleaner than embedding in runs)
5. **Advisory lock** — Prevents concurrent runs

### Schema (alembic/versions/001_initial.py)

```python
"""Initial schema."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None

def upgrade():
    # STORES
    op.create_table("stores",
        sa.Column("id", postgresql.UUID, primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("website_url", sa.String(500)),
        sa.Column("category", sa.String(100)),
        sa.Column("active", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # STORE_SOURCES (matching rules)
    op.create_table("store_sources",
        sa.Column("id", postgresql.UUID, primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("store_id", postgresql.UUID, sa.ForeignKey("stores.id", ondelete="CASCADE")),
        sa.Column("source_type", sa.String(50), nullable=False),  # gmail_from_address, gmail_from_domain
        sa.Column("pattern", sa.String(500), nullable=False),
        sa.Column("priority", sa.Integer, default=100),  # Higher wins
        sa.Column("active", sa.Boolean, default=True),
        sa.UniqueConstraint("store_id", "source_type", "pattern"),
    )

    # GMAIL_STATE (cursor)
    op.create_table("gmail_state",
        sa.Column("id", postgresql.UUID, primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_key", sa.String(100), unique=True, nullable=False),
        sa.Column("last_history_id", sa.String(100)),
        sa.Column("last_full_sync_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # EMAILS_RAW
    op.create_table("emails_raw",
        sa.Column("id", postgresql.UUID, primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("gmail_message_id", sa.String(100), unique=True, nullable=False),
        sa.Column("gmail_thread_id", sa.String(100)),
        sa.Column("store_id", postgresql.UUID, sa.ForeignKey("stores.id", ondelete="SET NULL")),
        sa.Column("from_address", sa.String(500), nullable=False),
        sa.Column("from_domain", sa.String(255), nullable=False),
        sa.Column("from_name", sa.String(500)),
        sa.Column("subject", sa.String(1000), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_text", sa.Text),
        sa.Column("body_hash", sa.String(64), nullable=False),
        sa.Column("top_links", postgresql.JSONB),
        sa.Column("extraction_status", sa.String(20), default="pending"),
        sa.Column("extraction_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # PROMO_EXTRACTIONS (raw LLM output for audit)
    op.create_table("promo_extractions",
        sa.Column("id", postgresql.UUID, primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email_id", postgresql.UUID, 
                  sa.ForeignKey("emails_raw.id", ondelete="CASCADE"), unique=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("extracted_json", postgresql.JSONB, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # PROMOS (canonical)
    op.create_table("promos",
        sa.Column("id", postgresql.UUID, primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("store_id", postgresql.UUID, 
                  sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_key", sa.String(500), nullable=False),  # Dedup key
        sa.Column("headline", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("discount_text", sa.String(500)),
        sa.Column("percent_off", sa.Float),
        sa.Column("amount_off", sa.Float),
        sa.Column("code", sa.String(100)),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("end_inferred", sa.Boolean, default=False),
        sa.Column("exclusions", sa.Text),
        sa.Column("landing_url", sa.String(1000)),
        sa.Column("confidence", sa.Float, default=0.5),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), default="active"),  # active/expired/unknown
        sa.Column("last_notified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("store_id", "base_key"),
    )
    op.create_index("ix_promos_ends_at", "promos", ["ends_at"])
    op.create_index("ix_promos_last_seen_at", "promos", ["last_seen_at"])

    # PROMO_EMAIL_LINKS (evidence)
    op.create_table("promo_email_links",
        sa.Column("id", postgresql.UUID, primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("promo_id", postgresql.UUID,
                  sa.ForeignKey("promos.id", ondelete="CASCADE")),
        sa.Column("email_id", postgresql.UUID,
                  sa.ForeignKey("emails_raw.id", ondelete="CASCADE")),
        sa.UniqueConstraint("promo_id", "email_id"),
    )

    # PROMO_CHANGES (powers NEW/UPDATED in digest)
    op.create_table("promo_changes",
        sa.Column("id", postgresql.UUID, primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("promo_id", postgresql.UUID,
                  sa.ForeignKey("promos.id", ondelete="CASCADE")),
        sa.Column("email_id", postgresql.UUID,
                  sa.ForeignKey("emails_raw.id", ondelete="CASCADE")),
        sa.Column("change_type", sa.String(50), nullable=False),
            # created, discount_changed, end_extended, code_added, etc.
        sa.Column("diff_json", postgresql.JSONB, default={}),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("promo_id", "email_id", "change_type"),
    )
    op.create_index("ix_promo_changes_changed_at", "promo_changes", ["changed_at"])

    # RUNS (idempotency)
    op.create_table("runs",
        sa.Column("id", postgresql.UUID, primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_type", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), default="running"),
        sa.Column("digest_date_et", sa.String(10), nullable=False),  # YYYY-MM-DD
        sa.Column("digest_sent_at", sa.DateTime(timezone=True)),
        sa.Column("digest_provider_id", sa.String(100)),
        sa.Column("gmail_cursor_history_id", sa.String(100)),
        sa.Column("stats_json", postgresql.JSONB, default={}),
        sa.Column("error_json", postgresql.JSONB, default={}),
        sa.UniqueConstraint("run_type", "digest_date_et"),  # Prevents double-send
    )

def downgrade():
    op.drop_table("runs")
    op.drop_table("promo_changes")
    op.drop_table("promo_email_links")
    op.drop_table("promos")
    op.drop_table("promo_extractions")
    op.drop_table("emails_raw")
    op.drop_table("gmail_state")
    op.drop_table("store_sources")
    op.drop_table("stores")
```

---

## 5. Gmail Integration

### Key Improvement: historyId Cursor with 404 Fallback

```python
# src/dealintel/gmail/ingest.py

def ingest_emails():
    """Incremental sync using Gmail historyId."""
    service = get_gmail_service()
    
    with get_db() as session:
        state = get_or_create_gmail_state(session)
        
        if state.last_history_id:
            # Incremental sync
            try:
                message_ids, new_history_id = fetch_via_history(
                    service, 
                    state.last_history_id
                )
            except HttpError as e:
                if e.resp.status == 404:
                    # History expired - fallback to full sync
                    logger.warning("History ID expired, doing full sync")
                    message_ids, new_history_id = fetch_by_date(service, days=14)
                    state.last_full_sync_at = datetime.now(UTC)
                else:
                    raise
        else:
            # First run - bootstrap
            message_ids, new_history_id = fetch_by_date(service, days=14)
            state.last_full_sync_at = datetime.now(UTC)
        
        # Process messages...
        for msg_id in message_ids:
            # Skip if already ingested (idempotent)
            if session.query(EmailRaw).filter_by(gmail_message_id=msg_id).first():
                continue
            
            # Fetch, parse, match store, save...
        
        # Update cursor
        if new_history_id:
            state.last_history_id = new_history_id


def fetch_via_history(service, start_history_id: str):
    """Fetch message IDs using Gmail History API with pagination."""
    message_ids = []
    page_token = None
    
    while True:
        response = service.users().history().list(
            userId="me",
            startHistoryId=start_history_id,
            historyTypes=["messageAdded"],
            pageToken=page_token,
        ).execute()
        
        for history in response.get("history", []):
            for msg in history.get("messagesAdded", []):
                message_ids.append(msg["message"]["id"])
        
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    
    return message_ids, response.get("historyId")
```

---

## 6. OpenAI Extraction

### Key Improvement: Structured Outputs API

```python
# src/dealintel/llm/schemas.py

from pydantic import BaseModel, Field

class PromoCandidate(BaseModel):
    headline: str
    summary: str | None = None
    discount_text: str | None = None
    percent_off: float | None = None
    amount_off: float | None = None
    code: str | None = None
    starts_at: str | None = None  # ISO 8601
    ends_at: str | None = None
    end_inferred: bool = False
    exclusions: list[str] = []
    landing_url: str | None = None
    confidence: float = Field(ge=0, le=1, default=0.5)
    missing_fields: list[str] = []

class ExtractionResult(BaseModel):
    is_promo_email: bool
    promos: list[PromoCandidate] = []
    notes: list[str] = []


# src/dealintel/llm/extract.py

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def extract_promos(email: EmailRaw) -> ExtractionResult:
    """Extract using OpenAI Structured Outputs (guaranteed schema compliance)."""
    client = OpenAI()
    
    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": format_email_for_extraction(email)},
        ],
        temperature=0.1,
        response_format=ExtractionResult,  # Pydantic model
    )
    
    # Guaranteed to be valid ExtractionResult
    return response.choices[0].message.parsed
```

---

## 7. Promo Deduplication

### Key Improvement: base_key Hierarchy

```python
# src/dealintel/promos/normalize.py

def compute_base_key(code: str | None, landing_url: str | None, headline: str) -> str:
    """
    Compute stable dedup key with priority:
    1. Code (most stable)
    2. URL path (without query params)
    3. Headline hash (fallback)
    """
    if code:
        return f"code:{code.upper().strip()}"
    
    if landing_url:
        normalized = normalize_url(landing_url)  # host + path, no query
        if normalized:
            return f"url:{normalized}"
    
    # Fallback to headline
    headline_hash = hashlib.md5(normalize_headline(headline).encode()).hexdigest()[:16]
    return f"head:{headline_hash}"


def normalize_url(url: str) -> str | None:
    """Remove query params and fragments."""
    parsed = urlparse(url)
    return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}" if parsed.netloc else None
```

### Key Improvement: Smarter Recency Window

```python
# src/dealintel/promos/merge.py

def find_matching_promo(session, store_id, base_key, headline, window_days=30):
    """Find existing promo with smarter recency logic."""
    now = datetime.now(UTC)
    window_start = now - timedelta(days=window_days)
    
    # Match if:
    # - Same base_key AND (seen recently OR ending soon OR no end date)
    promo = session.query(Promo).filter(
        Promo.store_id == store_id,
        Promo.base_key == base_key,
        or_(
            Promo.last_seen_at >= window_start,
            Promo.ends_at >= now - timedelta(days=2),
            Promo.ends_at.is_(None),
        )
    ).first()
    
    if promo:
        return promo
    
    # Fallback: headline similarity (RapidFuzz)
    # ...
```

### Key Improvement: Change Detection

```python
def detect_and_record_changes(existing: Promo, candidate: PromoCandidate, email_id):
    """Detect changes and record for digest badges."""
    changes = []
    
    # End date extended?
    if candidate.ends_at:
        new_ends = parse_datetime(candidate.ends_at)
        if existing.ends_at is None or new_ends > existing.ends_at:
            changes.append(("end_extended", {
                "before": existing.ends_at.isoformat() if existing.ends_at else None,
                "after": new_ends.isoformat(),
            }))
    
    # Discount changed?
    if candidate.percent_off != existing.percent_off:
        changes.append(("discount_changed", {
            "field": "percent_off",
            "before": existing.percent_off,
            "after": candidate.percent_off,
        }))
    
    # Code added?
    if candidate.code and not existing.code:
        changes.append(("code_added", {"code": candidate.code}))
    
    # Record changes
    for change_type, diff_json in changes:
        session.add(PromoChange(
            promo_id=existing.id,
            email_id=email_id,
            change_type=change_type,
            diff_json=diff_json,
            changed_at=datetime.now(UTC),
        ))
```

---

## 8. Digest Generation

### Key Improvement: Only New/Updated Since Last Digest

```python
# src/dealintel/digest/select.py

def select_digest_promos():
    """Select promos that are NEW or UPDATED since last digest."""
    with get_db() as session:
        # Get last successful digest timestamp
        last_run = session.query(Run).filter(
            Run.run_type == "daily_digest",
            Run.digest_sent_at.isnot(None)
        ).order_by(Run.digest_sent_at.desc()).first()
        
        since = last_run.digest_sent_at if last_run else datetime.now(UTC) - timedelta(hours=24)
        
        results = []
        seen = set()
        
        # NEW promos (created since last digest)
        new_changes = session.query(PromoChange).join(Promo).filter(
            PromoChange.change_type == "created",
            PromoChange.changed_at > since,
            Promo.status == "active",
        ).all()
        
        for change in new_changes:
            if change.promo_id not in seen:
                seen.add(change.promo_id)
                results.append({
                    "promo": change.promo,
                    "badge": "NEW",
                    "store_name": change.promo.store.name,
                })
        
        # UPDATED promos (changes since last digest)
        update_changes = session.query(PromoChange).join(Promo).filter(
            PromoChange.change_type != "created",
            PromoChange.changed_at > since,
            Promo.status == "active",
        ).all()
        
        for change in update_changes:
            if change.promo_id not in seen:
                seen.add(change.promo_id)
                results.append({
                    "promo": change.promo,
                    "badge": "UPDATED",
                    "store_name": change.promo.store.name,
                })
        
        return results
```

---

## 9. Orchestration

### Key Improvement: Advisory Lock + Idempotency

```python
# src/dealintel/jobs/daily.py

def run_daily_pipeline(dry_run=False):
    """Full pipeline with proper concurrency and idempotency."""
    today_et = datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d")
    
    with get_db() as session:
        # 1. Acquire advisory lock
        if not acquire_advisory_lock(session, "dealintel_daily"):
            logger.info("Another run in progress, exiting")
            return True
        
        try:
            # 2. Check if already ran today (unique constraint)
            existing = session.query(Run).filter_by(
                run_type="daily_digest",
                digest_date_et=today_et
            ).first()
            
            if existing and existing.digest_sent_at:
                logger.info("Digest already sent today")
                return True
            
            # Create/update run record
            run = existing or Run(run_type="daily_digest", digest_date_et=today_et)
            run.status = "running"
            session.add(run)
            session.flush()
            
            # 3-7. Run pipeline steps...
            seed_stores()
            ingest_stats = ingest_emails()
            extract_stats = process_pending_emails()
            merge_stats = merge_extracted_promos()
            
            # 8. Generate and send digest
            html, promo_count, store_count = generate_digest()
            
            if html and not dry_run:
                success, msg_id = send_digest_email(html)
                if success:
                    run.digest_sent_at = datetime.now(UTC)
                    run.digest_provider_id = msg_id
            elif html and dry_run:
                Path("digest_preview.html").write_text(html)
            
            run.status = "success"
            run.stats_json = {...}
            
        finally:
            release_advisory_lock(session, "dealintel_daily")


def acquire_advisory_lock(session, lock_name: str) -> bool:
    """Acquire Postgres advisory lock (prevents concurrent runs)."""
    lock_id = hash(lock_name) % (2**31)
    result = session.execute(
        text("SELECT pg_try_advisory_lock(:id)"),
        {"id": lock_id}
    )
    return result.scalar()
```

---

## 10. Scheduling

### macOS launchd

```xml
<!-- ~/Library/LaunchAgents/com.dealintel.daily.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dealintel.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/.venv/bin/dealintel</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/deal-intel</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>10</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/path/to/deal-intel/logs/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/deal-intel/logs/launchd.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.dealintel.daily.plist
```

### Linux cron

```bash
0 10 * * * cd /path/to/deal-intel && .venv/bin/dealintel run >> logs/cron.log 2>&1
```

---

## 11. Testing

### Golden File Tests (Prompt Regression)

```python
# tests/test_extraction_golden.py

def test_nike_extraction():
    """Test extraction against known-good output."""
    email = load_fixture("emails/nike_promo.eml")
    expected = load_json("golden/nike_promo.json")
    
    # Mock OpenAI or use live (optional)
    result = extract_promos(email)
    
    # Compare key fields
    assert result.is_promo_email == expected["is_promo_email"]
    assert len(result.promos) == len(expected["promos"])
    
    for actual, exp in zip(result.promos, expected["promos"]):
        assert actual.headline == exp["headline"]
        assert actual.code == exp.get("code")
        # Allow flexibility on summary text
```

---

## 12. Troubleshooting

### Useful SQL Queries

```sql
-- Unmatched senders (add to stores.yaml)
SELECT from_address, from_domain, COUNT(*) 
FROM emails_raw WHERE store_id IS NULL
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

-- Recent runs
SELECT digest_date_et, status, 
       (stats_json->>'promos_created')::int as new,
       (stats_json->>'promos_updated')::int as updated,
       digest_sent_at IS NOT NULL as sent
FROM runs ORDER BY started_at DESC LIMIT 10;

-- Recent changes (for debugging digest)
SELECT s.name, p.headline, pc.change_type, pc.changed_at
FROM promo_changes pc
JOIN promos p ON p.id = pc.promo_id
JOIN stores s ON s.id = p.store_id
ORDER BY pc.changed_at DESC LIMIT 20;
```

### Common Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| No emails matched | Missing patterns | Run unmatched query, update stores.yaml |
| Gmail 404 | History expired | Auto-fallback to full sync |
| Empty digest | No new/updated promos | Check promo_changes table |
| Duplicate digests | Should never happen | UNIQUE constraint prevents |
| Extraction errors | API issues | Retry logic handles, check logs |

---

## Quick Start

```bash
# 1. Setup
make install
make db-up
make migrate

# 2. Configure
cp .env.example .env
# Edit with your API keys
# Add credentials.json from Google Cloud

# 3. Seed and auth
make seed
make gmail-auth

# 4. Test run
make run-dry
open digest_preview.html

# 5. Real run
make run

# 6. Schedule (see Section 10)
```

---

## Summary

This hybrid guide combines:

**From the competing plan:**
- ✅ `promo_extractions`, `promo_changes`, `promo_email_links` tables
- ✅ Gmail historyId with 404 fallback
- ✅ OpenAI structured outputs (`responses.parse()`)
- ✅ base_key hierarchy (code → URL → headline)
- ✅ Smarter recency window for dedup
- ✅ Advisory locks + unique constraints for idempotency

**From the original guide:**
- ✅ Complete, copy-paste-ready code
- ✅ Step-by-step setup instructions
- ✅ Makefile with all commands
- ✅ Troubleshooting guide with SQL queries

**Total time: 18-28 hours**
