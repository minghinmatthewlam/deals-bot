# Deal Intelligence

Automated promotional email aggregation and daily digest delivery system.

Deal Intelligence monitors your Gmail inbox for promotional emails from configured stores, extracts deal information using AI, deduplicates offers, and delivers a digest of new and updated promotions (HTML + notifications).

---

## Quick Start

```bash
# One-command setup (installs, starts DB, migrates, seeds, writes .env if missing)
make setup

# Or step-by-step:
# 1. Install dependencies
make install

# 2. Start PostgreSQL
make db-up

# 3. Run database migrations
make migrate

# 4. Seed store configurations
make seed

# 5. Authenticate with Gmail (opens browser)
make gmail-auth

# 6. Test run (no emails sent)
make run-dry
```

Optional: interactive setup for store selection.

```bash
.venv/bin/python -m dealintel.cli init
```

Test notifications:

```bash
.venv/bin/dealintel notify test
```

Schedule weekly run (macOS):

```bash
.venv/bin/dealintel schedule weekly --time 12:00 --weekday sun
```

Check if a weekly run is still in progress:

```bash
pgrep -fl "dealintel weekly"
```

---

## Prerequisites

- Python 3.11+
- Docker (for PostgreSQL)
- Gmail account with API access enabled
- OpenAI API key
- SendGrid API key (optional, for email delivery)
- Playwright browsers (for browser automation)

---

## Configuration

### 1. Create Environment File

Copy the example and fill in your API keys:

```bash
cp .env.example .env
```

Required variables:

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key for deal extraction |

Optional variables:

| Variable | Description |
|----------|-------------|
| `SENDGRID_API_KEY` | SendGrid API key for digest delivery |
| `DIGEST_RECIPIENT` | Email address to receive digests |
| `DIGEST_FROM_EMAIL` | Verified sender email in SendGrid |
| `INGEST_GMAIL` | Enable Gmail ingestion (`true`/`false`) |
| `INGEST_WEB` | Enable web ingestion (`true`/`false`) |
| `INGEST_INBOUND` | Enable inbound .eml ingestion (`true`/`false`) |
| `INGEST_IGNORE_ROBOTS` | Ignore robots.txt for web sources (`true`/`false`) |
| `NEWSLETTER_SERVICE_EMAIL` | Service inbox address to use for newsletter signups |
| `BROWSER_HEADLESS` | Run Playwright headless (`true`/`false`) |
| `HUMAN_ASSIST_DIR` | Directory for human-assist tasks |
| `GMAIL_LOOKBACK_DAYS` | Days of Gmail history to scan on initial/expired sync |
| `GMAIL_MAX_MESSAGES` | Max Gmail messages to ingest per run (testing throttle) |
| `EXTRACT_MAX_EMAILS` | Max pending emails to extract per run (testing throttle) |
| `WEB_DEFAULT_CRAWL_DELAY_SECONDS` | Default crawl delay between requests |
| `WEB_DEFAULT_MAX_REQUESTS_PER_RUN` | Max web requests per run |
| `NOTIFY_EMAIL` | Enable SendGrid email delivery (`true`/`false`) |
| `NOTIFY_MACOS` | Enable macOS notifications (`true`/`false`) |
| `NOTIFY_MACOS_MODE` | `auto`, `terminal-notifier`, or `osascript` |
| `NOTIFY_TELEGRAM` | Enable Telegram notifications (`true`/`false`) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (required for Telegram notifications) |
| `TELEGRAM_CHAT_ID` | Telegram chat id (required for Telegram notifications) |

### 2. Configure Stores

Edit `stores.yaml` to add stores you want to track:

```yaml
stores:
  - slug: nike
    name: Nike
    sources:
      - type: gmail_from_address
        pattern: nike@email.nike.com
        priority: 100
      - type: gmail_from_domain
        pattern: nike.com
        priority: 50
```

Source types:
- `gmail_from_address`: Exact email address match (highest priority)
- `gmail_from_domain`: Domain match (fallback)

Run `make seed` after editing to apply changes.

### 2.1 Store Allowlist (Per-user Preferences)

Use `preferences.yaml` to restrict which stores are included in runs:

```yaml
stores:
  allowlist: ["cos", "corridor", "nike"]
```

CLI helpers:

```bash
.venv/bin/python -m dealintel.cli stores list
.venv/bin/python -m dealintel.cli stores search nike
.venv/bin/python -m dealintel.cli stores allowlist --set cos corridor nike
.venv/bin/python -m dealintel.cli sources report --store nike
```

### 2.5 Browser Automation (Playwright)

Install browser binaries once:

```bash
.venv/bin/playwright install chromium
```

### 3. Gmail API Setup

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the Gmail API
3. Create OAuth 2.0 credentials (Desktop app)
4. Download credentials to `credentials.json`
5. Run `make gmail-auth` to complete OAuth flow

---

## Development

### Common Commands

```bash
make help          # Show all available commands
make install       # Install dependencies
make db-up         # Start PostgreSQL container
make db-down       # Stop PostgreSQL container
make db-shell      # Open psql shell
make migrate       # Run database migrations
make seed          # Load store configurations
make run           # Run full pipeline
make run-dry       # Run without sending emails
make weekly        # Run weekly pipeline (newsletter + tiered web ingest)
make newsletter-subscribe # Run newsletter subscription agent
make confirmations # Poll for newsletter confirmation emails
make test          # Run test suite
make lint          # Run linters
make format        # Auto-format code
```

### Project Structure

```
src/dealintel/
├── __init__.py
├── cli.py              # Typer CLI entrypoint
├── config.py           # Pydantic settings
├── db.py               # Database connection & advisory locks
├── models.py           # SQLAlchemy ORM models
├── gmail/
│   ├── auth.py         # OAuth flow
│   ├── ingest.py       # Email fetching & store matching
│   └── parse.py        # Email body parsing
├── llm/
│   ├── schemas.py      # Pydantic models for extraction
│   └── extract.py      # OpenAI structured outputs
├── promos/
│   ├── normalize.py    # URL/headline normalization
│   └── merge.py        # Promo deduplication
├── digest/
│   ├── select.py       # Select promos for digest
│   └── render.py       # Jinja2 template rendering
├── outbound/
│   └── sendgrid_client.py  # Email delivery
└── jobs/
    └── daily.py        # Pipeline orchestrator
```

### Database Models

| Model | Description |
|-------|-------------|
| `Store` | Retail stores to track |
| `StoreSource` | Email matching rules per store |
| `GmailState` | Gmail sync cursor (historyId) |
| `EmailRaw` | Raw ingested emails |
| `PromoExtraction` | LLM extraction results |
| `Promo` | Deduplicated promotions |
| `PromoEmailLink` | Links promos to source emails |
| `PromoChange` | Change history for badges |
| `Run` | Pipeline execution records |

---

## Architecture

### Data Flow

```
Gmail Inbox → Ingest → Extract (OpenAI) → Merge/Dedupe → Digest → Notifications (HTML / macOS / Telegram / Email)
```

1. **Ingest**: Fetch new emails using Gmail API with historyId cursor
2. **Match**: Associate emails with stores using configured sources
3. **Extract**: Use OpenAI structured outputs to extract deal details
4. **Merge**: Deduplicate promos using base_key hierarchy (code > URL > headline)
5. **Track**: Record changes for NEW/UPDATED badges
6. **Render**: Generate HTML digest with Jinja2
7. **Send**: Deliver via SendGrid

### Deduplication Strategy

Promos are deduplicated using a **base_key** hierarchy:

1. **Promo code** (highest priority): `code:SAVE25`
2. **Landing URL path**: `url:/sale/winter`
3. **Headline hash** (fallback): `head:abc123...`

This ensures the same promotion seen across multiple emails is tracked as one entity.

### Concurrency Control

- **Advisory locks**: PostgreSQL `pg_try_advisory_lock()` prevents concurrent pipeline runs
- **Unique constraints**: `(run_type, digest_date_et)` prevents duplicate digests

### Incremental Sync

Gmail sync uses `historyId` for efficient incremental fetches:
- Normal operation: Fetch only messages since last `historyId`
- History expired (404): Fall back to full sync of last 14 days
- State persisted in `gmail_state` table

---

## Scheduling

### macOS (launchd)

```bash
# Install the launch agent
cp scheduling/com.dealintel.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.dealintel.daily.plist

# Check status
launchctl list | grep dealintel
```

### Linux (cron)

```bash
# Edit crontab
crontab -e

# Add daily run at 8 AM
0 8 * * * cd /path/to/deals-bot && .venv/bin/dealintel run >> logs/cron.log 2>&1
```

See `scheduling/README.md` for detailed instructions.

---

## Troubleshooting

### Quick Diagnostics

```bash
# Check system status
.venv/bin/dealintel status

# View recent runs
make db-shell
# Then: SELECT * FROM runs ORDER BY started_at DESC LIMIT 10;
```

### Common Issues

| Issue | Likely Cause | Solution |
|-------|--------------|----------|
| No emails matched | Sender not in stores.yaml | Add domain/address to stores.yaml, run `make seed` |
| History ID expired | Gmail cursor too old (>7 days) | Automatic fallback to full sync |
| Empty digest | No new/updated promos | Check `promo_changes` table |
| Extraction errors | OpenAI API issues | Check API key, credits, rate limits |

See `docs/RUNBOOK.md` for detailed troubleshooting.

---

## Testing

```bash
# Run all tests
make test

# Run with coverage
pytest --cov=dealintel --cov-report=html

# Run specific test file
pytest tests/test_normalize.py -v

# Run golden file tests
pytest tests/test_extraction_golden.py -v
```

### Test Structure

- `tests/test_*.py` - Unit tests
- `tests/test_integration.py` - End-to-end flow tests
- `tests/test_extraction_golden.py` - LLM output regression tests
- `tests/fixtures/` - Test data (emails, etc.)
- `tests/golden/` - Expected extraction outputs

---

## License

MIT
