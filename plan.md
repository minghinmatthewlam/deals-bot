# Deal Intelligence — Complete Implementation Guide

This document serves as the **single source of truth** for understanding and extending the Deal Intelligence system. It covers both the existing Gmail-based MVP and the planned multi-source acquisition system.

**Document Structure:**
- **Part 1**: Current MVP Implementation (Gmail-based) — What exists today
- **Part 2**: Multi-Source Acquisition Plan — What to build next

---

# PART 1: CURRENT MVP IMPLEMENTATION

This section documents the existing Gmail-based MVP that is fully implemented and working.

## 1. Architecture Overview

### Daily Pipeline Flow (Current)

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

### Current Directory Structure

```
deals-bot/
├── pyproject.toml
├── docker-compose.yml
├── Makefile
├── stores.yaml              # Store definitions + email matching rules
├── .env                     # Environment variables (not committed)
├── .env.example             # Template for .env
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── templates/
│   └── digest.html.j2       # Email digest template
├── src/dealintel/
│   ├── __init__.py
│   ├── cli.py               # Typer CLI commands
│   ├── config.py            # Pydantic Settings
│   ├── db.py                # Database session management
│   ├── models.py            # SQLAlchemy ORM models
│   ├── seed.py              # Store seeding from YAML
│   ├── gmail/
│   │   ├── auth.py          # Gmail OAuth token management
│   │   ├── ingest.py        # Gmail API ingestion
│   │   └── parse.py         # Email body parsing
│   ├── llm/
│   │   ├── schemas.py       # Pydantic models for extraction
│   │   └── extract.py       # OpenAI structured extraction
│   ├── promos/
│   │   ├── normalize.py     # base_key computation
│   │   └── merge.py         # Dedupe/merge logic
│   ├── digest/
│   │   ├── select.py        # Select NEW/UPDATED promos
│   │   └── render.py        # Jinja2 rendering
│   ├── outbound/
│   │   └── sendgrid_client.py
│   └── jobs/
│       └── daily.py         # Pipeline orchestration
└── tests/
    ├── conftest.py
    ├── test_integration.py
    ├── test_digest.py
    ├── test_parse.py
    ├── test_normalize.py
    └── test_extraction_golden.py
```

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

### Environment Variables

```bash
# .env.example
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

## 3. Database Schema

### Tables Overview

| Table | Purpose |
|-------|---------|
| `stores` | Retailers/brands that send promotional emails |
| `store_sources` | Email matching rules (from_address, from_domain patterns) |
| `gmail_state` | Gmail sync cursor (historyId) |
| `emails_raw` | Raw ingested emails |
| `promo_extractions` | Raw LLM output for audit/debugging |
| `promos` | Canonical promotional offers |
| `promo_email_links` | Many-to-many: promos ↔ source emails |
| `promo_changes` | Change tracking for NEW/UPDATED badges |
| `runs` | Pipeline run tracking for idempotency |

### Key Schema Details

**emails_raw** (central to multi-source plan):
```sql
CREATE TABLE emails_raw (
    id UUID PRIMARY KEY,
    gmail_message_id VARCHAR(100) UNIQUE NOT NULL,  -- Used as idempotency key
    gmail_thread_id VARCHAR(100),
    store_id UUID REFERENCES stores(id),
    from_address VARCHAR(500) NOT NULL,
    from_domain VARCHAR(255) NOT NULL,
    from_name VARCHAR(500),
    subject VARCHAR(1000) NOT NULL,
    received_at TIMESTAMP WITH TIME ZONE NOT NULL,
    body_text TEXT,
    body_hash VARCHAR(64) NOT NULL,
    top_links JSONB,
    extraction_status VARCHAR(20) DEFAULT 'pending',  -- pending/success/error
    extraction_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

**promos** (canonical deals):
```sql
CREATE TABLE promos (
    id UUID PRIMARY KEY,
    store_id UUID REFERENCES stores(id) NOT NULL,
    base_key VARCHAR(500) NOT NULL,      -- Dedup key: code:X or url:X or head:X
    headline VARCHAR(500) NOT NULL,
    summary TEXT,
    discount_text VARCHAR(500),
    percent_off FLOAT,
    amount_off FLOAT,
    code VARCHAR(100),
    starts_at TIMESTAMP WITH TIME ZONE,
    ends_at TIMESTAMP WITH TIME ZONE,
    end_inferred BOOLEAN DEFAULT FALSE,
    exclusions TEXT,
    landing_url VARCHAR(1000),
    confidence FLOAT DEFAULT 0.5,
    first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(20) DEFAULT 'active',  -- active/expired/unknown
    last_notified_at TIMESTAMP WITH TIME ZONE,
    UNIQUE(store_id, base_key)
);
```

---

## 4. Key Implementation Details

### 4.1 Gmail Ingestion (`src/dealintel/gmail/ingest.py`)

Uses Gmail History API with cursor-based sync:
- Stores `last_history_id` in `gmail_state` table
- On 404 (history expired), falls back to full sync (last 14 days)
- Matches emails to stores via `store_sources` patterns

### 4.2 OpenAI Extraction (`src/dealintel/llm/extract.py`)

Uses OpenAI Structured Outputs API for guaranteed schema compliance:
```python
response = client.beta.chat.completions.parse(
    model="gpt-4o-mini",
    messages=[...],
    response_format=ExtractionResult,  # Pydantic model
)
```

### 4.3 Promo Deduplication (`src/dealintel/promos/normalize.py`)

**base_key hierarchy** (most stable → least stable):
1. `code:{PROMO_CODE}` — Promo codes are most stable
2. `url:{normalized_url}` — URL path without query params
3. `head:{headline_hash}` — MD5 of normalized headline (fallback)

### 4.4 Change Detection (`src/dealintel/promos/merge.py`)

Tracks changes for digest badges:
- `created` — New promo
- `discount_changed` — Percent/amount changed
- `end_extended` — End date pushed out
- `code_added` — Promo code added to existing deal

### 4.5 Pipeline Orchestration (`src/dealintel/jobs/daily.py`)

- Uses Postgres advisory lock to prevent concurrent runs
- `UNIQUE (run_type, digest_date_et)` prevents double-sends
- Stores stats in `runs.stats_json`

---

## 5. Quick Start (Current MVP)

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
```

---

## 6. Troubleshooting

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

-- Extraction status summary
SELECT extraction_status, COUNT(*)
FROM emails_raw
GROUP BY extraction_status;
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

# PART 2: MULTI-SOURCE ACQUISITION PLAN

This section documents the planned migration from Gmail-only to multi-source acquisition (web crawlers + inbound email + optional Gmail).

## Executive Summary

### Goal
Replace Gmail OAuth as the default acquisition path with:
1. **Web crawlers** for public sale/deals pages (primary)
2. **Deals Inbox** via .eml files (secondary, for email-exclusive deals)
3. **Gmail OAuth** as optional legacy path (opt-in)

### Why
- **Eliminate trust friction**: Users don't want to grant Gmail access
- **Proactive discovery**: Find deals without being subscribed to newsletters
- **Broader coverage**: Crawl airline deals, store sale pages directly

### Approach: H1 (Reuse EmailRaw)
Instead of creating new tables, we reuse `emails_raw` with different `gmail_message_id` prefixes:

| Source | gmail_message_id Format | Example |
|--------|------------------------|---------|
| Gmail | `<real gmail id>` | `18f2a3b4c5d6e7f8` |
| Web | `web:<url_hash>:<body_hash>` | `web:a1b2c3d4:efgh5678` |
| Inbound | `inbound:<file_hash>` | `inbound:9876543210abcdef` |

**No database migrations required** — the existing schema handles all sources.

---

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **HTML structure changes break scrapers** | Medium | High | Use LLM extraction (robust to layout); alert on error spike |
| **Rate limiting/blocking by sites** | Low | Medium | 1 request/30s per domain; fixed user-agent; respect robots.txt |
| **Sale pages with dynamic JS content** | Medium | Medium | SSENSE may need Playwright; others are static. Fail gracefully. |
| **Content changes daily (product rotation)** | High | Medium | Dedupe by URL + body_hash; accept some churn |
| **Flight deal aggregator format changes** | Low | High | Start with Secret Flying only; monitor for failures |
| **Scope creep** | Medium | High | Ship Milestone 2 before adding flights; validate value |
| **Inbound email spam/noise** | Medium | Low | Allowlist by from_domain; existing store matching filters |

---

## Milestone 0: Guardrails + Baseline

**Goal**: Establish baseline before any code changes. Prevent breaking existing functionality.

### 0.1 Confirm Repo State

```bash
git status
# Note: .gitignore and stores.yaml are modified (expected)
# Do NOT revert without explicit approval
```

### 0.2 Run Baseline Tests

```bash
make test      # All tests must pass
make lint      # No lint errors
make typecheck # No type errors
```

**If any fail**: Stop and fix before proceeding.

### 0.3 Document Current Pipeline Wiring

| Component | File:Line | Current Behavior |
|-----------|-----------|------------------|
| Gmail import | `src/dealintel/jobs/daily.py:11` | `from dealintel.gmail.ingest import ingest_emails` |
| Ingest call | `src/dealintel/jobs/daily.py:119` | `stats["ingest"] = ingest_emails()` |
| EmailRaw unique key | `src/dealintel/models.py:73` | `gmail_message_id` UNIQUE NOT NULL |
| Extraction FK | `src/dealintel/models.py:100` | `PromoExtraction.email_id` unique (1:1) |

### 0.4 Acceptance Criteria

- [ ] `make test` passes
- [ ] `make lint` passes
- [ ] `make typecheck` passes
- [ ] Agent understands files listed above

---

## Milestone 1: Source Router (Gmail Optional)

**Goal**: Gmail becomes opt-in. Pipeline runs without Gmail credentials.

### 1.1 Add Config Toggles

**File**: `src/dealintel/config.py`

```python
"""Configuration management using Pydantic Settings."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+psycopg://dealintel:dealintel_dev@localhost:5432/dealintel"

    # OpenAI
    openai_api_key: SecretStr
    openai_model: str = "gpt-4o-mini"

    # SendGrid
    sendgrid_api_key: SecretStr

    # Email addresses
    sender_email: str
    recipient_email: str

    # === SOURCE TOGGLES (NEW) ===
    ingest_gmail: bool = False      # Gmail is now OPT-IN
    ingest_web: bool = True         # Web crawlers are DEFAULT
    ingest_inbound: bool = False    # Deals inbox is OPT-IN

    # Gmail OAuth (only needed if ingest_gmail=True)
    gmail_credentials_path: str = "credentials.json"
    gmail_token_path: str = "token.json"


settings = Settings()
```

### 1.2 Update .env.example

**File**: `.env.example`

Add after existing variables:
```bash
# === Source toggles ===
INGEST_GMAIL=false
INGEST_WEB=true
INGEST_INBOUND=false
```

### 1.3 Create Source Router

**File**: `src/dealintel/ingest/__init__.py`
```python
"""Ingestion router module."""
```

**File**: `src/dealintel/ingest/router.py`

```python
"""Route ingestion across enabled sources."""

import structlog

from dealintel.config import settings

logger = structlog.get_logger()


def ingest_all_sources() -> dict[str, dict]:
    """Aggregate ingestion stats from all enabled sources.

    Returns:
        dict with keys for each source type, each containing stats dict
    """
    stats: dict[str, dict] = {}

    # Gmail (opt-in)
    if settings.ingest_gmail:
        logger.info("Ingesting from Gmail...")
        from dealintel.gmail.ingest import ingest_emails
        stats["gmail"] = ingest_emails()
    else:
        logger.info("Gmail ingestion disabled")
        stats["gmail"] = {"enabled": False}

    # Web crawlers (default)
    if settings.ingest_web:
        logger.info("Ingesting from web sources...")
        from dealintel.web.ingest import ingest_web_sources
        stats["web"] = ingest_web_sources()
    else:
        logger.info("Web ingestion disabled")
        stats["web"] = {"enabled": False}

    # Inbound email (opt-in)
    if settings.ingest_inbound:
        logger.info("Ingesting from inbound directory...")
        from dealintel.inbound.ingest import ingest_inbound_eml_dir
        stats["inbound"] = ingest_inbound_eml_dir()
    else:
        logger.info("Inbound ingestion disabled")
        stats["inbound"] = {"enabled": False}

    return stats
```

### 1.4 Wire Pipeline to Router

**File**: `src/dealintel/jobs/daily.py`

**Change line 11** from:
```python
from dealintel.gmail.ingest import ingest_emails
```
to:
```python
from dealintel.ingest.router import ingest_all_sources
```

**Change line 119** from:
```python
stats["ingest"] = ingest_emails()
```
to:
```python
stats["ingest"] = ingest_all_sources()
```

### 1.5 Update CLI Stats Display

**File**: `src/dealintel/cli.py`

Update stats display section (around line 98):

```python
# Display per-source ingestion stats
ingest = stats.get("ingest") or {}
for source_name, source_stats in ingest.items():
    enabled = source_stats.get("enabled", True)
    table.add_row("Ingest", f"{source_name}", "enabled" if enabled else "disabled")
    if enabled and isinstance(source_stats, dict):
        if "new" in source_stats:
            table.add_row("", f"  new", str(source_stats["new"]))
        if "skipped" in source_stats:
            table.add_row("", f"  skipped", str(source_stats["skipped"]))
        if "errors" in source_stats:
            table.add_row("", f"  errors", str(source_stats["errors"]))
```

### 1.6 Create Stub Web Module

**File**: `src/dealintel/web/__init__.py`
```python
"""Web crawling module."""
```

**File**: `src/dealintel/web/ingest.py`
```python
"""Web source ingestion (stub - implemented in Milestone 2)."""

import structlog

logger = structlog.get_logger()


def ingest_web_sources() -> dict:
    """Stub implementation - returns empty stats."""
    logger.warning("Web ingestion not yet implemented")
    return {
        "enabled": True,
        "sources": 0,
        "new": 0,
        "skipped": 0,
        "errors": 0,
    }
```

### 1.7 Acceptance Criteria

```bash
# Set Gmail to disabled
export INGEST_GMAIL=false
export INGEST_WEB=true

# Remove Gmail credentials (if they exist)
mv credentials.json credentials.json.bak 2>/dev/null || true
mv token.json token.json.bak 2>/dev/null || true

# Run pipeline
make run-dry

# Expected: Pipeline completes without crashing
# Expected: Logs show "Gmail ingestion disabled"
# Expected: Logs show "Web ingestion not yet implemented"
```

### 1.8 Phase Gate SQL

```sql
-- Verify run completed
SELECT status, stats_json FROM runs ORDER BY started_at DESC LIMIT 1;
-- Expected: status='success', stats_json contains ingest.gmail.enabled=false
```

---

## Milestone 2: Web Crawler (COS + Corridor)

**Goal**: Crawl two clothing store sale pages and produce promos.

### 2.1 Add httpx Dependency

**File**: `pyproject.toml`

Add to dependencies:
```toml
"httpx>=0.27.0",
```

Run:
```bash
.venv/bin/pip install -e ".[dev]"
```

### 2.2 Add Web Sources to stores.yaml

**File**: `stores.yaml`

Update COS entry:
```yaml
  # COS
  - slug: cos
    name: COS
    website_url: https://cos.com
    category: apparel
    sources:
      - type: gmail_from_address
        pattern: news@us.e.cos.com
        priority: 50
      - type: gmail_from_domain
        pattern: us.e.cos.com
        priority: 40
      # NEW: Web source
      - type: web_url
        pattern: https://www.cos.com/en_usd/sale.html
        priority: 100
```

Update Corridor entry:
```yaml
  # Corridor NYC
  - slug: corridor
    name: Corridor
    website_url: https://corridornyc.com
    category: apparel
    sources:
      - type: gmail_from_address
        pattern: info@corridornyc.com
        priority: 50
      - type: gmail_from_domain
        pattern: corridornyc.com
        priority: 40
      # NEW: Web source
      - type: web_url
        pattern: https://corridornyc.com/collections/sale
        priority: 100
```

### 2.3 Implement Web Fetching

**File**: `src/dealintel/web/fetch.py`

```python
"""HTTP fetching with retries and caching headers."""

from dataclasses import dataclass

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger()

USER_AGENT = "DealIntelBot/0.1 (+https://github.com/user/deals-bot; single-user MVP)"
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB


@dataclass(frozen=True)
class FetchResult:
    """Result of fetching a URL."""
    final_url: str
    status_code: int
    text: str | None
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    elapsed_ms: int | None = None
    truncated: bool = False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
    reraise=True,
)
def fetch_url(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    """Fetch URL with retries, redirects, and conditional GET support."""
    headers = {"User-Agent": USER_AGENT}

    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = client.get(url)
            elapsed_ms = int(response.elapsed.total_seconds() * 1000)

            # Handle 304 Not Modified
            if response.status_code == 304:
                return FetchResult(
                    final_url=str(response.url),
                    status_code=304,
                    text=None,
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                    elapsed_ms=elapsed_ms,
                )

            response.raise_for_status()

            # Truncate if too large
            content = response.text
            truncated = False
            if len(content) > MAX_CONTENT_LENGTH:
                content = content[:MAX_CONTENT_LENGTH] + "\n\n[TRUNCATED]"
                truncated = True
                logger.warning("Content truncated", url=url)

            return FetchResult(
                final_url=str(response.url),
                status_code=response.status_code,
                text=content,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                elapsed_ms=elapsed_ms,
                truncated=truncated,
            )

    except httpx.HTTPStatusError as e:
        logger.error("HTTP error", url=url, status=e.response.status_code)
        raise
    except httpx.RequestError as e:
        logger.error("Request error", url=url, error=str(e))
        return FetchResult(
            final_url=url,
            status_code=0,
            text=None,
            error=str(e),
        )
```

### 2.4 Implement HTML Parsing

**File**: `src/dealintel/web/parse.py`

```python
"""HTML parsing for web pages."""

from dataclasses import dataclass

import html2text
from bs4 import BeautifulSoup

from dealintel.gmail.parse import extract_top_links


@dataclass(frozen=True)
class ParsedPage:
    """Parsed web page content."""
    title: str | None
    body_text: str
    top_links: list[str] | None
    canonical_url: str | None


def html_to_text(html: str) -> str:
    """Convert HTML to plain text, stripping scripts/styles."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()

    cleaned_html = str(soup)

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0

    return converter.handle(cleaned_html)


def extract_canonical_url(html: str) -> str | None:
    """Extract canonical URL from <link rel="canonical">."""
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        return canonical["href"]
    return None


def parse_web_html(html: str) -> ParsedPage:
    """Parse web page HTML into structured content."""
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else None
    canonical_url = extract_canonical_url(html)
    links = extract_top_links(html)
    top_links = links if links else None
    body_text = html_to_text(html)

    return ParsedPage(
        title=title,
        body_text=body_text,
        top_links=top_links,
        canonical_url=canonical_url,
    )
```

### 2.5 Implement Web Ingestion

**File**: `src/dealintel/web/ingest.py`

```python
"""Web source ingestion - creates synthetic EmailRaw rows."""

from datetime import UTC, datetime
import hashlib

import structlog

from dealintel.db import get_db
from dealintel.gmail.parse import compute_body_hash
from dealintel.models import EmailRaw, StoreSource
from dealintel.web.fetch import fetch_url
from dealintel.web.parse import parse_web_html

logger = structlog.get_logger()

WEB_SOURCE_TYPES = {"web_url"}


def _web_message_id(canonical_url: str, body_hash: str) -> str:
    """Generate stable unique ID for web content.

    Format: web:<url_hash_16>:<body_hash_16>
    """
    url_key = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
    return f"web:{url_key}:{body_hash[:16]}"


def ingest_web_sources() -> dict:
    """Ingest all active web sources.

    For each web_url source:
    1. Fetch the page
    2. Parse HTML to text
    3. Check if content changed (via body_hash)
    4. Create EmailRaw row if new content
    """
    stats = {
        "enabled": True,
        "sources": 0,
        "new": 0,
        "skipped": 0,
        "unchanged": 0,
        "errors": 0,
    }

    with get_db() as session:
        sources = (
            session.query(StoreSource)
            .filter(
                StoreSource.active == True,  # noqa: E712
                StoreSource.source_type.in_(WEB_SOURCE_TYPES),
            )
            .all()
        )
        stats["sources"] = len(sources)

        if not sources:
            logger.info("No web sources configured")
            return stats

        for source in sources:
            url = source.pattern
            store = source.store

            try:
                logger.info("Fetching web source", url=url, store=store.slug)

                result = fetch_url(url)

                if result.error:
                    logger.error("Fetch failed", url=url, error=result.error)
                    stats["errors"] += 1
                    continue

                if result.status_code == 304:
                    logger.debug("Page unchanged (304)", url=url)
                    stats["unchanged"] += 1
                    continue

                if not result.text:
                    logger.warning("Empty response", url=url)
                    stats["errors"] += 1
                    continue

                parsed = parse_web_html(result.text)
                body_text = parsed.body_text

                body_hash = compute_body_hash(body_text)
                canonical_url = parsed.canonical_url or result.final_url
                message_id = _web_message_id(canonical_url, body_hash)

                existing = session.query(EmailRaw).filter_by(
                    gmail_message_id=message_id
                ).first()

                if existing:
                    logger.debug("Content unchanged", url=url)
                    stats["skipped"] += 1
                    continue

                subject = f"[WEB] {store.name}: {parsed.title or 'Sale Page'}"
                formatted_body = f"""Source: Web Crawl
URL: {canonical_url}
Fetched: {datetime.now(UTC).isoformat()}
Store: {store.name}

{body_text}"""

                email = EmailRaw(
                    gmail_message_id=message_id,
                    gmail_thread_id=None,
                    store_id=source.store_id,
                    from_address="crawler@dealintel.local",
                    from_domain="dealintel.local",
                    from_name="DealIntel Crawler",
                    subject=subject,
                    received_at=datetime.now(UTC),
                    body_text=formatted_body,
                    body_hash=body_hash,
                    top_links=parsed.top_links,
                    extraction_status="pending",
                )
                session.add(email)
                stats["new"] += 1

                logger.info("Web content ingested", url=url, store=store.slug)

            except Exception as e:
                logger.exception("Web ingest failed", url=url)
                stats["errors"] += 1

    return stats
```

### 2.6 Add Unit Tests

**File**: `tests/test_web_ingest.py`

```python
"""Tests for web ingestion (no network calls)."""

from unittest.mock import patch, MagicMock
import pytest

from dealintel.web.fetch import FetchResult
from dealintel.web.ingest import ingest_web_sources, _web_message_id
from dealintel.web.parse import parse_web_html


COS_SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Sale | COS</title></head>
<body>
    <h1>End of Season Sale</h1>
    <p>Up to 50% off selected items</p>
</body>
</html>
"""


class TestWebMessageId:
    def test_deterministic(self):
        id1 = _web_message_id("https://example.com", "abc123")
        id2 = _web_message_id("https://example.com", "abc123")
        assert id1 == id2

    def test_format(self):
        msg_id = _web_message_id("https://example.com", "abcdef123456")
        assert msg_id.startswith("web:")
        parts = msg_id.split(":")
        assert len(parts) == 3


class TestParseWebHtml:
    def test_extracts_title(self):
        parsed = parse_web_html(COS_SAMPLE_HTML)
        assert parsed.title == "Sale | COS"

    def test_extracts_body_text(self):
        parsed = parse_web_html(COS_SAMPLE_HTML)
        assert "End of Season Sale" in parsed.body_text
        assert "50% off" in parsed.body_text
```

### 2.7 Acceptance Criteria

```bash
make seed
export INGEST_GMAIL=false
export INGEST_WEB=true
make run-dry
open digest_preview.html
```

**Expected**:
- COS and Corridor pages fetched
- `EmailRaw` rows created with `gmail_message_id` starting with `web:`
- Promos extracted and appear in digest

### 2.8 Phase Gate SQL

```sql
-- Verify web content was ingested
SELECT gmail_message_id, store_id, subject, extraction_status
FROM emails_raw
WHERE gmail_message_id LIKE 'web:%'
ORDER BY created_at DESC LIMIT 10;

-- Verify store matching worked
SELECT COUNT(*) FROM emails_raw
WHERE gmail_message_id LIKE 'web:%' AND store_id IS NULL;
-- Expected: 0

-- Re-run and verify no duplicates
SELECT COUNT(*) FROM emails_raw WHERE gmail_message_id LIKE 'web:%';
-- Should be same count after second run
```

---

## Milestone 3: Sale Page Parser Enhancement

**Goal**: Improve extraction quality for e-commerce sale pages.

### 3.1 The Problem

Generic html2text on a sale page produces:
```
Sale

Women  Men  Everything Else

[Product grid with 100+ items]
```

This gives the LLM no useful signal for extracting a meaningful promo.

### 3.2 The Solution

Synthesize a structured summary:
```
SSENSE Sale Page

Banner Text: "Sale"

Observed Discounts (sample of 10 items):
- Acne Studios Jacket: $900 → $540 (40% off)
- Our Legacy Shirt: $220 → $132 (40% off)

Discount Range: 30-70% off
Landing URL: https://www.ssense.com/en-us/sale
```

### 3.3 Implementation

**File**: `src/dealintel/web/parse_sale.py`

```python
"""Specialized parser for e-commerce sale pages."""

from dataclasses import dataclass
import re

from bs4 import BeautifulSoup
import structlog

logger = structlog.get_logger()


@dataclass
class ProductSample:
    name: str
    original_price: float | None
    sale_price: float | None
    discount_percent: int | None


@dataclass
class SalePageSummary:
    title: str | None
    banner_text: list[str]
    product_samples: list[ProductSample]
    discount_range: tuple[int, int] | None
    categories: list[str]
    landing_url: str


def parse_sale_page(html: str, url: str) -> SalePageSummary:
    """Parse e-commerce sale page into structured summary."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else None
    banners = _extract_banner_text(soup)
    products = _sample_products(soup, limit=10)

    discounts = [p.discount_percent for p in products if p.discount_percent]
    discount_range = (min(discounts), max(discounts)) if discounts else None

    categories = []
    for crumb in soup.select('[class*="breadcrumb"] a')[:5]:
        cat = crumb.get_text(strip=True)
        if cat and len(cat) < 50:
            categories.append(cat)

    return SalePageSummary(
        title=title,
        banner_text=banners,
        product_samples=products,
        discount_range=discount_range,
        categories=categories,
        landing_url=url,
    )


def format_sale_summary_for_extraction(summary: SalePageSummary) -> str:
    """Format SalePageSummary as text for LLM extraction."""
    lines = [
        f"Sale Page: {summary.title or 'Unknown'}",
        f"URL: {summary.landing_url}",
        "",
    ]

    if summary.banner_text:
        lines.append("Banner/Hero Text:")
        for banner in summary.banner_text:
            lines.append(f"  - {banner}")
        lines.append("")

    if summary.product_samples:
        lines.append(f"Product Samples ({len(summary.product_samples)} items):")
        for p in summary.product_samples:
            parts = [f"  - {p.name}"]
            if p.original_price and p.sale_price:
                parts.append(f": ${p.original_price:.0f} → ${p.sale_price:.0f}")
            if p.discount_percent:
                parts.append(f" ({p.discount_percent}% off)")
            lines.append("".join(parts))
        lines.append("")

    if summary.discount_range:
        min_d, max_d = summary.discount_range
        lines.append(f"Observed Discount Range: {min_d}% - {max_d}% off")

    return "\n".join(lines)


def _extract_banner_text(soup: BeautifulSoup) -> list[str]:
    """Extract prominent banner/hero text."""
    banners = []
    selectors = ['h1', '.hero-title', '.banner-title', '[class*="hero"]']

    for selector in selectors:
        for el in soup.select(selector)[:3]:
            text = el.get_text(strip=True)
            if text and len(text) < 200:
                banners.append(text)

    return list(dict.fromkeys(banners))[:5]


def _sample_products(soup: BeautifulSoup, limit: int = 10) -> list[ProductSample]:
    """Sample products from sale page."""
    samples = []

    product_selectors = [
        '[class*="product-tile"]',
        '[class*="product-card"]',
        '.product',
    ]

    products = []
    for selector in product_selectors:
        products = soup.select(selector)
        if products:
            break

    for product in products[:limit]:
        try:
            name_el = product.select_one('[class*="name"], [class*="title"], h2, h3')
            name = name_el.get_text(strip=True) if name_el else None

            if not name or len(name) > 100:
                continue

            samples.append(ProductSample(
                name=name,
                original_price=None,
                sale_price=None,
                discount_percent=None,
            ))
        except Exception:
            continue

    return samples
```

### 3.4 Update Web Ingestion

In `src/dealintel/web/ingest.py`, detect sale pages and use the enhanced parser:

```python
from dealintel.web.parse_sale import parse_sale_page, format_sale_summary_for_extraction

# In ingest loop:
is_sale_page = (
    store.category == "apparel" and
    any(kw in url.lower() for kw in ["sale", "clearance", "outlet"])
)

if is_sale_page:
    sale_summary = parse_sale_page(result.text, canonical_url)
    body_text = format_sale_summary_for_extraction(sale_summary)
else:
    body_text = parsed.body_text
```

---

## Milestone 4: Deals Inbox (.eml Ingestion)

**Goal**: Ingest promotional emails from .eml files without Gmail OAuth.

### 4.1 Why .eml Files?

- **Testable**: No external services needed
- **Provider-agnostic**: SendGrid, SES, Mailgun can all write .eml
- **Debuggable**: Files on disk are easy to inspect
- **Cron-friendly**: Fits existing batch model

### 4.2 Implementation

**File**: `src/dealintel/inbound/__init__.py`
```python
"""Inbound email ingestion module."""
```

**File**: `src/dealintel/inbound/parse_eml.py`

```python
"""Parse .eml files into structured data."""

from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from datetime import datetime

import html2text

from dealintel.gmail.parse import extract_top_links


@dataclass
class ParsedEmail:
    subject: str
    from_address: str
    from_name: str | None
    received_at: datetime | None
    body_text: str | None
    top_links: list[str] | None


def parse_eml(raw_bytes: bytes) -> ParsedEmail:
    """Parse raw .eml bytes into structured data."""
    msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    subject = msg.get("subject") or "(no subject)"
    from_header = msg.get("from") or ""
    from_address, from_name = _parse_from_address(from_header)

    received_at = None
    if msg.get("date"):
        try:
            received_at = parsedate_to_datetime(msg.get("date"))
        except Exception:
            pass

    body_text, top_links = _get_best_body(msg)

    return ParsedEmail(
        subject=subject,
        from_address=from_address,
        from_name=from_name,
        received_at=received_at,
        body_text=body_text,
        top_links=top_links,
    )


def _parse_from_address(from_header: str) -> tuple[str, str | None]:
    from_header = from_header.strip()
    if "<" in from_header and ">" in from_header:
        parts = from_header.split("<")
        name = parts[0].strip().strip('"') or None
        address = parts[1].rstrip(">").strip()
    else:
        address = from_header
        name = None
    return address.lower(), name


def _get_best_body(msg) -> tuple[str | None, list[str] | None]:
    text_part = None
    html_part = None

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and text_part is None:
                text_part = part
            elif ctype == "text/html" and html_part is None:
                html_part = part
    else:
        ctype = msg.get_content_type()
        if ctype == "text/plain":
            text_part = msg
        elif ctype == "text/html":
            html_part = msg

    if text_part:
        return text_part.get_content(), None

    if html_part:
        html = html_part.get_content()
        links = extract_top_links(html)
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.body_width = 0
        return converter.handle(html), links if links else None

    return None, None
```

**File**: `src/dealintel/inbound/ingest.py`

```python
"""Ingest emails from .eml files in a directory."""

from datetime import UTC, datetime
import hashlib
from pathlib import Path

import structlog

from dealintel.db import get_db
from dealintel.gmail.ingest import match_store
from dealintel.gmail.parse import compute_body_hash
from dealintel.models import EmailRaw
from dealintel.inbound.parse_eml import parse_eml

logger = structlog.get_logger()

DEFAULT_EML_DIR = "inbound_eml"


def _inbound_message_id(raw_bytes: bytes) -> str:
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    return f"inbound:{raw_hash[:60]}"


def ingest_inbound_eml_dir(eml_dir: str = DEFAULT_EML_DIR) -> dict:
    """Ingest all .eml files from a directory."""
    stats = {
        "enabled": True,
        "files": 0,
        "new": 0,
        "matched": 0,
        "unmatched": 0,
        "skipped": 0,
        "errors": 0,
    }

    path = Path(eml_dir)
    if not path.exists():
        logger.info("Inbound directory does not exist", path=eml_dir)
        return stats

    eml_files = sorted(path.glob("*.eml"))
    stats["files"] = len(eml_files)

    with get_db() as session:
        for file_path in eml_files:
            try:
                raw_bytes = file_path.read_bytes()
                message_id = _inbound_message_id(raw_bytes)

                if session.query(EmailRaw).filter_by(gmail_message_id=message_id).first():
                    stats["skipped"] += 1
                    continue

                parsed = parse_eml(raw_bytes)
                from_domain = parsed.from_address.split("@")[1] if "@" in parsed.from_address else ""
                store_id = match_store(session, parsed.from_address, from_domain)

                body_text = parsed.body_text or ""
                body_hash = compute_body_hash(body_text)

                email = EmailRaw(
                    gmail_message_id=message_id,
                    gmail_thread_id=None,
                    store_id=store_id,
                    from_address=parsed.from_address,
                    from_domain=from_domain,
                    from_name=parsed.from_name,
                    subject=parsed.subject,
                    received_at=parsed.received_at or datetime.now(UTC),
                    body_text=body_text,
                    body_hash=body_hash,
                    top_links=parsed.top_links,
                    extraction_status="pending",
                )
                session.add(email)
                stats["new"] += 1

                if store_id:
                    stats["matched"] += 1
                else:
                    stats["unmatched"] += 1

            except Exception as e:
                logger.exception("Failed to process", file=str(file_path))
                stats["errors"] += 1

    return stats
```

### 4.3 Add CLI Command

**File**: `src/dealintel/cli.py`

```python
@app.command()
def inbound_import(
    eml_dir: str = typer.Option("inbound_eml", "--dir", "-d")
):
    """Import emails from .eml files."""
    from dealintel.inbound.ingest import ingest_inbound_eml_dir
    stats = ingest_inbound_eml_dir(eml_dir)
    console.print(stats)
```

### 4.4 Acceptance Criteria

```bash
mkdir -p inbound_eml
# Drop a .eml file into the directory
export INGEST_INBOUND=true
make run-dry
```

---

## Milestone 5: Flights v1 (Basic Ingestion)

**Goal**: Ingest airline deal pages without filtering.

### 5.1 Add Airlines to stores.yaml

```yaml
  # Secret Flying (deal aggregator)
  - slug: secret-flying
    name: Secret Flying
    website_url: https://secretflying.com
    category: travel
    sources:
      - type: web_url
        pattern: https://secretflying.com/posts/
        priority: 100

  # United Airlines
  - slug: united
    name: United Airlines
    website_url: https://www.united.com
    category: travel
    sources:
      - type: web_url
        pattern: https://www.united.com/en/us/deals
        priority: 100

  # Delta
  - slug: delta
    name: Delta Air Lines
    website_url: https://www.delta.com
    category: travel
    sources:
      - type: web_url
        pattern: https://www.delta.com/us/en/deals-and-offers
        priority: 100
```

### 5.2 No Code Changes Needed

The existing web crawler handles airline pages. Promos will be basic but functional.

---

## Milestone 6: Flights v2 (Structured Filtering)

**Goal**: Add flight-specific extraction schema and filter by preferences.

### 6.1 Extend Extraction Schema

**File**: `src/dealintel/llm/schemas.py`

```python
class FlightDeal(BaseModel):
    """Flight-specific deal information."""
    origins: list[str] = Field(default_factory=list)
    destinations: list[str] = Field(default_factory=list)
    destination_region: str | None = None
    price_usd: float | None = None
    travel_window: str | None = None
    booking_url: str | None = None


class PromoCandidate(BaseModel):
    # ... existing fields ...

    vertical: str = Field("retail", description="retail|flight|other")
    flight: FlightDeal | None = None
```

### 6.2 Create Preferences File

**File**: `preferences.yaml`

```yaml
flights:
  origins: ["SFO", "OAK", "SJC"]
  destination_regions: ["Europe", "Asia"]
  max_price_usd:
    Europe: 600
    Asia: 1000
```

### 6.3 Implement Preferences Loader

**File**: `src/dealintel/prefs.py`

```python
from pathlib import Path
import yaml
from pydantic import BaseModel, Field


class FlightPrefs(BaseModel):
    origins: list[str] = Field(default_factory=lambda: ["SFO"])
    destination_regions: list[str] = Field(default_factory=lambda: ["Europe", "Asia"])
    max_price_usd: dict[str, float] = Field(default_factory=dict)


class Preferences(BaseModel):
    flights: FlightPrefs = Field(default_factory=FlightPrefs)


def load_preferences(path: str = "preferences.yaml") -> Preferences:
    p = Path(path)
    if not p.exists():
        return Preferences()
    return Preferences.model_validate(yaml.safe_load(p.read_text()) or {})
```

---

## Stats Structure

### Run.stats_json Format

```json
{
  "date": "2024-12-24",
  "dry_run": true,
  "ingest": {
    "gmail": {"enabled": false},
    "web": {
      "enabled": true,
      "sources": 6,
      "new": 2,
      "skipped": 3,
      "unchanged": 1,
      "errors": 0
    },
    "inbound": {
      "enabled": true,
      "files": 3,
      "new": 2,
      "matched": 2,
      "unmatched": 0,
      "skipped": 1,
      "errors": 0
    }
  },
  "extract": {"processed": 4, "succeeded": 4, "failed": 0},
  "merge": {"created": 2, "updated": 1, "unchanged": 1},
  "digest": {"promo_count": 3, "store_count": 2},
  "success": true
}
```

---

## File Manifest

### New Files to Create

| File | Milestone | Purpose |
|------|-----------|---------|
| `src/dealintel/ingest/__init__.py` | M1 | Module init |
| `src/dealintel/ingest/router.py` | M1 | Source router |
| `src/dealintel/web/__init__.py` | M2 | Module init |
| `src/dealintel/web/fetch.py` | M2 | HTTP fetching |
| `src/dealintel/web/parse.py` | M2 | HTML parsing |
| `src/dealintel/web/ingest.py` | M2 | Web ingestion |
| `src/dealintel/web/parse_sale.py` | M3 | Sale page parser |
| `src/dealintel/inbound/__init__.py` | M4 | Module init |
| `src/dealintel/inbound/parse_eml.py` | M4 | .eml parsing |
| `src/dealintel/inbound/ingest.py` | M4 | Inbound ingestion |
| `src/dealintel/prefs.py` | M6 | Preferences loader |
| `preferences.yaml` | M6 | User preferences |
| `tests/test_web_ingest.py` | M2 | Web tests |
| `tests/test_inbound_ingest.py` | M4 | Inbound tests |

### Files to Modify

| File | Milestone | Changes |
|------|-----------|---------|
| `src/dealintel/config.py` | M1 | Add source toggles |
| `src/dealintel/jobs/daily.py` | M1 | Use router |
| `src/dealintel/cli.py` | M1, M4 | Stats display, inbound-import |
| `stores.yaml` | M2, M5 | Add web sources, airlines |
| `pyproject.toml` | M2 | Add httpx |
| `.env.example` | M1 | Add toggle env vars |
| `src/dealintel/llm/schemas.py` | M6 | Add FlightDeal |
| `src/dealintel/llm/extract.py` | M6 | Update prompt |

---

## Quick Reference

### Commands

```bash
# Current MVP (Gmail-based)
make install && make db-up && make migrate
make seed && make gmail-auth
make run-dry

# After Milestone 1+ (Multi-source)
export INGEST_GMAIL=false
export INGEST_WEB=true
export INGEST_INBOUND=false
make run-dry
```

### Verify Source Type

```sql
-- Check what sources exist
SELECT
    CASE
        WHEN gmail_message_id LIKE 'web:%' THEN 'web'
        WHEN gmail_message_id LIKE 'inbound:%' THEN 'inbound'
        ELSE 'gmail'
    END as source_type,
    COUNT(*)
FROM emails_raw
GROUP BY 1;
```
