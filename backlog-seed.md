# Deal Intelligence MVP - Master Backlog

Type: epic
Priority: 1

---

This is the master execution plan for building the Deal Intelligence MVP - a system that:
1. Ingests promotional emails from Gmail
2. Extracts deal information via OpenAI
3. Deduplicates and tracks promo changes
4. Generates and sends daily digest emails

**Architecture Philosophy:**
- Idempotent: UNIQUE constraints prevent double-sends
- Cursor-based: Gmail historyId with 404 fallback
- Canonical promos: One row per promo even from 10 emails
- Change tracking: promo_changes table powers NEW/UPDATED badges
- Graceful degradation: One extraction failure doesn't kill the run
- Concurrency safe: Postgres advisory lock prevents race conditions

**Source of Truth:** plan.md at repo root contains the complete implementation guide.

---

# Phase 1: Prerequisites & Environment Setup

Type: epic
Priority: 1

---

Before any code can be written, we need to ensure all required tools, accounts, and credentials are in place. This phase is about validation and setup - no code is written here, but everything must be ready for the subsequent phases.

**Why this matters:** Skipping prerequisites leads to blocked work later. Better to validate everything upfront.

**Human involvement required:** Account creation, API key generation, and OAuth consent screen setup cannot be automated.

---

## 1.1: Verify Required Tools

Type: task
Priority: 1

---

**Goal:** Confirm Python 3.11+, Docker 20.0+, and Docker Compose 2.0+ are installed and working.

**Acceptance Criteria:**
- [ ] `python3 --version` returns 3.11 or higher
- [ ] `docker --version` returns 20.0 or higher
- [ ] `docker compose version` returns 2.0 or higher

**Commands to run:**
```bash
python3 --version  # 3.11+
docker --version   # 20.0+
docker compose version  # 2.0+
```

**If any tool is missing:** Stop and ask the human to install it before proceeding.

**Rationale:** Python 3.11 is required for modern type hints and performance. Docker is used for Postgres. These are non-negotiable prerequisites.

---

## 1.2: Google Cloud Console Setup

Type: task
Priority: 1
Deps: 1.1

---

**Goal:** Ensure Google Cloud project exists with Gmail API enabled and OAuth credentials created.

**Why Gmail API:** The system reads promotional emails from a dedicated Gmail inbox. The Gmail API provides structured access with history-based incremental sync.

**Required Steps (human must complete):**
1. Create or select a Google Cloud project at console.cloud.google.com
2. Enable the Gmail API for the project
3. Configure OAuth consent screen (can be "Internal" for personal use or "External" in testing mode)
4. Create OAuth 2.0 Client ID (Desktop application type)
5. Download the credentials.json file

**Acceptance Criteria:**
- [ ] credentials.json file exists and is valid JSON
- [ ] File contains `installed.client_id` and `installed.client_secret`

**Security Note:** credentials.json should NEVER be committed to git. Add to .gitignore.

**Tip:** For personal use, a dedicated deals inbox (e.g., yourname.deals@gmail.com) keeps promotional emails separate from your main inbox.

---

## 1.3: OpenAI API Setup

Type: task
Priority: 1
Deps: 1.1

---

**Goal:** Ensure OpenAI API access is configured for structured extraction.

**Why OpenAI:** The system uses OpenAI's structured outputs API (via Pydantic models) to extract promotional information from email content. This provides guaranteed schema compliance.

**Model Selection:** gpt-4o-mini is recommended for cost efficiency. The structured outputs feature ensures we always get valid JSON matching our Pydantic schema.

**Required Steps (human must complete):**
1. Create or access OpenAI account at platform.openai.com
2. Generate an API key with sufficient credits
3. Note: Structured outputs require a compatible model (gpt-4o-mini, gpt-4o)

**Acceptance Criteria:**
- [ ] OPENAI_API_KEY is available (starts with "sk-")
- [ ] API key has sufficient credits for extraction calls

**Cost Estimate:** ~$0.15/1M input tokens, ~$0.60/1M output tokens for gpt-4o-mini. A typical email extraction uses ~2K tokens, so 100 emails/day ≈ $0.03/day.

---

## 1.4: SendGrid API Setup

Type: task
Priority: 1
Deps: 1.1

---

**Goal:** Ensure SendGrid is configured for sending digest emails.

**Why SendGrid:** Reliable email delivery with good deliverability. The free tier (100 emails/day) is sufficient for personal use.

**Required Steps (human must complete):**
1. Create SendGrid account at sendgrid.com
2. Verify a sender email address (or domain)
3. Generate an API key with "Mail Send" permission

**Acceptance Criteria:**
- [ ] SENDGRID_API_KEY is available (starts with "SG.")
- [ ] Sender email is verified in SendGrid

**Alternative:** For initial development, the system supports --dry-run mode which saves the digest HTML to a file instead of sending.

---

## 1.5: Create .env Configuration

Type: task
Priority: 1
Deps: 1.2, 1.3, 1.4

---

**Goal:** Create .env file with all required configuration values.

**Why .env:** Centralizes all secrets and configuration. Never commit to git.

**Required Variables:**
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

**Acceptance Criteria:**
- [ ] .env file exists at repo root
- [ ] All required variables are set with valid values
- [ ] .gitignore includes .env

**Security Checklist:**
- [ ] .env is in .gitignore
- [ ] credentials.json is in .gitignore
- [ ] token.json is in .gitignore

---

# Phase 2: Project Scaffolding

Type: epic
Priority: 1
Deps: Phase 1: Prerequisites & Environment Setup

---

Set up the project structure, dependencies, and local development infrastructure. This phase produces a runnable skeleton with database, but no business logic yet.

**Key Decisions Made:**
- **Package manager:** pip with pyproject.toml (standard, no poetry/pipenv complexity)
- **Database:** PostgreSQL 16 via Docker (production-grade, advisory locks, JSONB)
- **ORM:** SQLAlchemy 2.0 with async support ready (not used initially for simplicity)
- **Migrations:** Alembic (standard for SQLAlchemy)

**Why not SQLite?** Advisory locks for concurrency safety require Postgres. Also, JSONB columns for storing extraction results and run stats.

---

## 2.1: Create Directory Structure

Type: task
Priority: 1

---

**Goal:** Create the complete directory structure as defined in plan.md.

**Structure:**
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
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── seed.py
│   ├── gmail/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── ingest.py
│   │   └── parse.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── schemas.py
│   │   └── extract.py
│   ├── promos/
│   │   ├── __init__.py
│   │   ├── normalize.py
│   │   └── merge.py
│   ├── digest/
│   │   ├── __init__.py
│   │   ├── select.py
│   │   └── render.py
│   ├── outbound/
│   │   ├── __init__.py
│   │   └── sendgrid_client.py
│   └── jobs/
│       ├── __init__.py
│       └── daily.py
└── tests/
    └── __init__.py
```

**Acceptance Criteria:**
- [ ] All directories created
- [ ] All __init__.py files created (can be empty initially)
- [ ] Structure matches plan.md exactly

**Rationale:** Clean separation of concerns:
- `gmail/` - All Gmail API interaction
- `llm/` - OpenAI extraction logic
- `promos/` - Deduplication and normalization
- `digest/` - Selection and rendering
- `outbound/` - Email sending
- `jobs/` - Orchestration

---

## 2.2: Create pyproject.toml

Type: task
Priority: 1
Deps: 2.1

---

**Goal:** Define project metadata and dependencies.

**Key Dependencies and Why:**
- `sqlalchemy>=2.0.0` - Modern ORM with 2.0 style queries
- `alembic>=1.13.0` - Database migrations
- `psycopg[binary]>=3.1.0` - Modern PostgreSQL driver (psycopg3)
- `google-api-python-client>=2.100.0` - Gmail API
- `google-auth-oauthlib>=1.2.0` - OAuth flow for Gmail
- `openai>=1.12.0` - Structured outputs support
- `sendgrid>=6.10.0` - Email sending
- `pydantic>=2.5.0` - Schema validation, used by OpenAI structured outputs
- `pydantic-settings>=2.1.0` - Environment variable loading
- `typer[all]>=0.9.0` - CLI framework with rich output
- `beautifulsoup4>=4.12.0` - HTML parsing for email content
- `html2text>=2024.2.26` - Convert HTML emails to text for LLM
- `jinja2>=3.1.0` - Digest email templating
- `rapidfuzz>=3.6.0` - Fuzzy string matching for promo dedup
- `structlog>=24.1.0` - Structured logging
- `tenacity>=8.2.0` - Retry logic for API calls
- `pytz>=2024.1` - Timezone handling for Eastern Time

**Entry Point:** `dealintel = "dealintel.cli:app"`

**Acceptance Criteria:**
- [ ] pyproject.toml matches plan.md spec
- [ ] `pip install -e .` succeeds
- [ ] `dealintel --help` runs

---

## 2.3: Create docker-compose.yml

Type: task
Priority: 1
Deps: 2.1

---

**Goal:** Define PostgreSQL container for local development.

**Configuration Choices:**
- `postgres:16-alpine` - Latest stable, minimal image
- Port 5432 exposed for local access
- Named volume for data persistence
- Healthcheck for reliable startup

**Credentials (dev only):**
- User: dealintel
- Password: dealintel_dev
- Database: dealintel

**Acceptance Criteria:**
- [ ] `docker compose up -d` starts Postgres
- [ ] `docker compose exec postgres psql -U dealintel` connects
- [ ] Database persists across container restarts

---

## 2.4: Create Makefile

Type: task
Priority: 1
Deps: 2.2, 2.3

---

**Goal:** Provide convenient commands for common operations.

**Commands:**
- `make install` - Create venv and install package
- `make db-up` - Start Postgres container
- `make db-shell` - Connect to Postgres
- `make migrate` - Run Alembic migrations
- `make seed` - Seed stores from stores.yaml
- `make gmail-auth` - Run OAuth flow
- `make run` - Run daily pipeline
- `make run-dry` - Dry run (no email send)
- `make test` - Run tests

**Acceptance Criteria:**
- [ ] All make commands work
- [ ] `make install && make db-up && make migrate` produces working setup

---

## 2.5: Create .env.example

Type: task
Priority: 1
Deps: 2.1

---

**Goal:** Document required environment variables without exposing secrets.

**Purpose:** New developers can copy to .env and fill in their values.

**Acceptance Criteria:**
- [ ] .env.example contains all required variables with placeholder values
- [ ] Comments explain each variable's purpose

---

## 2.6: Create config.py with Pydantic Settings

Type: task
Priority: 1
Deps: 2.2

---

**Goal:** Centralize configuration loading with validation.

**Implementation:**
- Use pydantic-settings for automatic .env loading
- Validate required fields at startup
- Provide typed access to all config values

**Key Fields:**
- database_url: PostgreSQL connection string
- openai_api_key: OpenAI API key
- openai_model: Model to use (default: gpt-4o-mini)
- sendgrid_api_key: SendGrid API key
- sender_email: From address for digests
- recipient_email: To address for digests
- gmail_credentials_path: Path to credentials.json
- gmail_token_path: Path to token.json (created after OAuth)

**Acceptance Criteria:**
- [ ] Config loads from .env
- [ ] Missing required fields raise clear errors
- [ ] Config is importable: `from dealintel.config import settings`

---

## 2.7: Create db.py Database Connection

Type: task
Priority: 1
Deps: 2.6

---

**Goal:** Set up SQLAlchemy engine and session management.

**Implementation:**
- Create engine from DATABASE_URL
- Provide context manager for session handling
- Use psycopg3 dialect

**Pattern:**
```python
with get_db() as session:
    # do work
    session.commit()
```

**Acceptance Criteria:**
- [ ] `get_db()` context manager works
- [ ] Connection to Postgres succeeds
- [ ] Sessions auto-rollback on exception

---

## 2.8: Create CLI Skeleton

Type: task
Priority: 1
Deps: 2.6

---

**Goal:** Set up Typer CLI with stub commands.

**Commands to stub:**
- `dealintel seed` - Seed stores (calls seed.py)
- `dealintel gmail-auth` - Run OAuth flow
- `dealintel run` - Run daily pipeline
- `dealintel run --dry-run` - Dry run mode

**Acceptance Criteria:**
- [ ] `dealintel --help` shows all commands
- [ ] Each command runs (can be no-op initially)

---

# Phase 3: Database Schema

Type: epic
Priority: 1
Deps: Phase 2: Project Scaffolding

---

Define and create the complete database schema using Alembic migrations. This is the data foundation for the entire system.

**Schema Design Principles:**
1. **UUIDs everywhere** - No sequential IDs that leak information
2. **Timestamps with timezone** - Always store in UTC
3. **JSONB for flexibility** - Store raw extractions, stats, diffs
4. **Unique constraints for idempotency** - Prevent duplicates at DB level
5. **Foreign keys with appropriate ON DELETE** - Maintain referential integrity

**Key Innovation:** The `promo_changes` table enables tracking NEW/UPDATED badges in digests by recording every significant change to a promo.

---

## 3.1: Create models.py with SQLAlchemy Models

Type: task
Priority: 1

---

**Goal:** Define all SQLAlchemy ORM models matching the schema.

**Models:**
1. **Store** - Retailer/brand (slug, name, website, category, active)
2. **StoreSource** - Matching rules (store_id, source_type, pattern, priority)
3. **GmailState** - Cursor state (user_key, last_history_id, last_full_sync_at)
4. **EmailRaw** - Ingested emails (gmail_message_id, store_id, from_address, subject, body_text, etc.)
5. **PromoExtraction** - Raw LLM output (email_id, model, extracted_json)
6. **Promo** - Canonical promos (store_id, base_key, headline, discount_text, code, dates, etc.)
7. **PromoEmailLink** - Many-to-many (promo_id, email_id)
8. **PromoChange** - Change tracking (promo_id, email_id, change_type, diff_json)
9. **Run** - Pipeline runs (run_type, digest_date_et, status, stats_json)

**Key Relationships:**
- Store has many StoreSource (matching rules)
- Store has many EmailRaw (matched emails)
- Store has many Promo (canonical promos)
- EmailRaw has one PromoExtraction
- Promo has many PromoEmailLink (evidence)
- Promo has many PromoChange (history)

**Acceptance Criteria:**
- [ ] All models defined with proper types
- [ ] Relationships defined with back_populates
- [ ] Unique constraints match schema

---

## 3.2: Create Alembic Initial Migration

Type: task
Priority: 1
Deps: 3.1

---

**Goal:** Create the initial migration that sets up all tables.

**Implementation:**
- Initialize Alembic: `alembic init alembic`
- Configure alembic.ini with DATABASE_URL from env
- Create migration file with all tables

**Tables to Create:**
1. stores
2. store_sources
3. gmail_state
4. emails_raw
5. promo_extractions
6. promos (with indexes on ends_at, last_seen_at)
7. promo_email_links
8. promo_changes (with index on changed_at)
9. runs

**Unique Constraints (critical for idempotency):**
- stores(slug)
- store_sources(store_id, source_type, pattern)
- gmail_state(user_key)
- emails_raw(gmail_message_id)
- promo_extractions(email_id)
- promos(store_id, base_key)
- promo_email_links(promo_id, email_id)
- promo_changes(promo_id, email_id, change_type)
- runs(run_type, digest_date_et)

**Acceptance Criteria:**
- [ ] `alembic upgrade head` succeeds
- [ ] All tables created in database
- [ ] Indexes and constraints in place

---

## 3.3: Create stores.yaml and Seed Script

Type: task
Priority: 1
Deps: 3.2

---

**Goal:** Define initial stores and matching rules in YAML, with script to upsert to database.

**stores.yaml Structure:**
```yaml
stores:
  - slug: nike
    name: Nike
    website_url: https://nike.com
    category: apparel
    sources:
      - type: gmail_from_address
        pattern: nike@email.nike.com
        priority: 100
      - type: gmail_from_domain
        pattern: nike.com
        priority: 50
```

**Seed Logic:**
- Upsert stores by slug (update if exists)
- Upsert sources by (store_id, type, pattern)
- Handle inactive stores gracefully

**Why YAML:** Human-editable, easy to add new stores. The seed script syncs this to the database on each run.

**Acceptance Criteria:**
- [ ] stores.yaml with at least 5 sample stores
- [ ] seed.py upserts correctly
- [ ] `dealintel seed` runs without error
- [ ] Re-running seed is idempotent

---

# Phase 4: Gmail Integration

Type: epic
Priority: 1
Deps: Phase 3: Database Schema

---

Implement Gmail authentication and email ingestion with cursor-based incremental sync.

**Key Innovation:** The history API with 404 fallback provides efficient incremental sync while gracefully handling expired cursors.

**Data Flow:**
1. Check gmail_state for last_history_id
2. If exists, use History API for incremental fetch
3. If 404 (expired), fallback to date-based full sync
4. For each message: fetch, parse headers, match store, save

---

## 4.1: Implement Gmail OAuth Flow

Type: task
Priority: 1

---

**Goal:** Create auth.py with OAuth flow for Gmail API access.

**Implementation:**
- Use google-auth-oauthlib for OAuth 2.0
- Scope: `https://www.googleapis.com/auth/gmail.readonly`
- Store token in token.json (auto-refresh)
- Handle first-time auth (opens browser)

**Flow:**
1. Check if token.json exists and is valid
2. If not, initiate OAuth flow with credentials.json
3. Open browser for user consent
4. Save token.json with access + refresh tokens

**Security:**
- Read-only scope (can't modify emails)
- Token refresh handled automatically
- credentials.json and token.json never committed

**Acceptance Criteria:**
- [ ] `dealintel gmail-auth` opens browser on first run
- [ ] token.json created after authorization
- [ ] Subsequent runs use cached token
- [ ] Token auto-refreshes when expired

---

## 4.2: Implement Gmail Service Factory

Type: task
Priority: 1
Deps: 4.1

---

**Goal:** Create function to get authenticated Gmail API service.

**Implementation:**
```python
def get_gmail_service():
    creds = load_or_refresh_credentials()
    return build('gmail', 'v1', credentials=creds)
```

**Acceptance Criteria:**
- [ ] Returns authenticated service object
- [ ] Handles token refresh transparently
- [ ] Raises clear error if not authenticated

---

## 4.3: Implement History-Based Incremental Fetch

Type: task
Priority: 1
Deps: 4.2

---

**Goal:** Implement fetch_via_history() for efficient incremental sync.

**How History API Works:**
- Gmail assigns a historyId to each change
- We store last_history_id in gmail_state
- history().list() returns all message changes since that ID
- Much faster than scanning all messages

**Implementation:**
```python
def fetch_via_history(service, start_history_id: str):
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

**Acceptance Criteria:**
- [ ] Fetches only new messages since last_history_id
- [ ] Handles pagination correctly
- [ ] Returns new historyId for next sync

---

## 4.4: Implement Date-Based Fallback Fetch

Type: task
Priority: 1
Deps: 4.2

---

**Goal:** Implement fetch_by_date() as fallback when history expires.

**When This Happens:**
- First run (no history ID)
- History ID expired (404 from History API)
- Manual full resync requested

**Implementation:**
- Use messages().list() with `after:` query
- Default: last 14 days
- Paginate through all results

**Acceptance Criteria:**
- [ ] Fetches messages from last N days
- [ ] Handles pagination
- [ ] Returns current historyId for future incremental syncs

---

## 4.5: Implement Email Ingestion Orchestrator

Type: task
Priority: 1
Deps: 4.3, 4.4

---

**Goal:** Create ingest_emails() that orchestrates the sync.

**Logic:**
1. Get or create gmail_state row
2. If last_history_id exists:
   - Try fetch_via_history()
   - On 404: log warning, fallback to fetch_by_date()
3. Else:
   - Do initial fetch_by_date()
4. For each message_id:
   - Skip if gmail_message_id already in emails_raw
   - Fetch full message
   - Parse headers and body
   - Match to store
   - Save to emails_raw
5. Update gmail_state with new history_id

**Acceptance Criteria:**
- [ ] First run bootstraps from date range
- [ ] Subsequent runs use incremental sync
- [ ] 404 handled gracefully with fallback
- [ ] Duplicate messages skipped (idempotent)

---

## 4.6: Implement Email Parsing

Type: task
Priority: 1
Deps: 4.5

---

**Goal:** Create parse.py with email parsing utilities.

**Parse Functions:**
- `parse_headers(message)` - Extract From, Subject, Date
- `parse_from_address(from_header)` - Extract email and name
- `parse_body(message)` - Extract text/plain or html2text from text/html
- `extract_top_links(html)` - Get first N links from HTML body
- `compute_body_hash(body)` - SHA256 of normalized body

**Challenges:**
- Multipart emails (prefer text/plain, fallback to HTML)
- Base64 encoding
- HTML to text conversion
- Unicode handling

**Acceptance Criteria:**
- [ ] Handles common email formats
- [ ] Extracts clean text from HTML emails
- [ ] Computes stable body hash

---

## 4.7: Implement Store Matching

Type: task
Priority: 1
Deps: 4.6

---

**Goal:** Match incoming emails to stores using source rules.

**Matching Logic:**
1. For each email, check from_address against store_sources
2. Check gmail_from_address first (exact match)
3. Then check gmail_from_domain (domain match)
4. Higher priority wins if multiple matches
5. Return matched store_id or None

**Why Priority Field:**
- Exact address match (priority 100) beats domain match (priority 50)
- Allows overrides for shared email providers

**Acceptance Criteria:**
- [ ] Exact address matches correctly
- [ ] Domain matching works
- [ ] Priority ordering correct
- [ ] Returns None for unmatched emails

---

# Phase 5: OpenAI Extraction

Type: epic
Priority: 1
Deps: Phase 4: Gmail Integration

---

Extract promotional information from emails using OpenAI's structured outputs API.

**Key Innovation:** Using Pydantic models with `response_format` guarantees the LLM output always matches our schema - no JSON parsing errors, no missing fields.

**Design Decision:** We store the raw extraction in promo_extractions before merging to canonical promos. This preserves evidence and enables debugging/regression testing.

---

## 5.1: Define Pydantic Extraction Schemas

Type: task
Priority: 1

---

**Goal:** Create schemas.py with Pydantic models for extraction.

**Models:**
```python
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
```

**Field Rationale:**
- `headline`: Required - main promo description
- `discount_text`: Human-readable discount (e.g., "25% off")
- `percent_off`/`amount_off`: Numeric for comparisons
- `code`: Promo code if any
- `ends_at`/`end_inferred`: Date and whether it was inferred vs explicit
- `exclusions`: Fine print restrictions
- `confidence`: LLM's self-assessed confidence
- `missing_fields`: What the LLM couldn't find

**Acceptance Criteria:**
- [ ] Models defined with proper types and defaults
- [ ] Validation works (confidence range, etc.)
- [ ] Can be used as OpenAI response_format

---

## 5.2: Create Extraction System Prompt

Type: task
Priority: 1
Deps: 5.1

---

**Goal:** Write effective system prompt for promo extraction.

**Prompt Should Cover:**
- Role: Email promo extraction specialist
- Task: Extract promotional offers from email content
- Output format: ExtractionResult schema
- Guidelines:
  - is_promo_email = false for non-promotional emails
  - Extract ALL distinct offers (some emails have multiple)
  - Parse dates carefully (interpret relative dates)
  - Infer end dates from context if not explicit (mark end_inferred=true)
  - Extract codes exactly as shown
  - Note any ambiguity in notes[]

**Quality Tips:**
- Include examples of good extractions
- Be explicit about edge cases (newsletter vs promo)
- Guide confidence scoring

**Acceptance Criteria:**
- [ ] System prompt produces accurate extractions
- [ ] Handles edge cases (multi-promo, no-promo)
- [ ] Notes capture useful context

---

## 5.3: Implement Email Formatting for LLM

Type: task
Priority: 1
Deps: 5.2

---

**Goal:** Create format_email_for_extraction() to prepare email content.

**Format:**
```
Store: {store_name}
Subject: {subject}
Date: {received_at}

{body_text (truncated to ~3000 chars)}

Top Links:
- {link1}
- {link2}
...
```

**Considerations:**
- Truncate very long emails (4K token budget)
- Include store context for better extraction
- Include top links as they often contain landing URLs

**Acceptance Criteria:**
- [ ] Produces clean, structured input for LLM
- [ ] Handles long emails gracefully
- [ ] Includes relevant context

---

## 5.4: Implement Extraction with Structured Outputs

Type: task
Priority: 1
Deps: 5.3

---

**Goal:** Create extract_promos() using OpenAI structured outputs.

**Implementation:**
```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def extract_promos(email: EmailRaw) -> ExtractionResult:
    client = OpenAI()

    response = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": format_email_for_extraction(email)},
        ],
        temperature=0.1,
        response_format=ExtractionResult,
    )

    return response.choices[0].message.parsed
```

**Key Points:**
- Use `beta.chat.completions.parse()` for structured outputs
- Pass Pydantic model as `response_format`
- Low temperature for consistency
- Retry with exponential backoff

**Acceptance Criteria:**
- [ ] Returns valid ExtractionResult
- [ ] Handles API errors with retry
- [ ] Logs extraction for debugging

---

## 5.5: Implement Batch Extraction Processing

Type: task
Priority: 1
Deps: 5.4

---

**Goal:** Create process_pending_emails() to extract promos from unprocessed emails.

**Logic:**
1. Query emails_raw where extraction_status = 'pending'
2. For each email:
   - Call extract_promos()
   - Save result to promo_extractions
   - Update email extraction_status to 'success' or 'error'
3. Continue even if one fails (graceful degradation)
4. Return stats: processed, succeeded, failed

**Graceful Degradation:**
- One extraction failure doesn't kill the run
- Record error in extraction_error field
- Allow retry on next run

**Acceptance Criteria:**
- [ ] Processes all pending emails
- [ ] Saves extractions to promo_extractions
- [ ] Handles individual failures gracefully
- [ ] Returns useful stats

---

# Phase 6: Promo Deduplication

Type: epic
Priority: 1
Deps: Phase 5: OpenAI Extraction

---

Transform raw extractions into canonical promos with intelligent deduplication and change tracking.

**Key Innovation:** The base_key hierarchy (code > URL > headline) provides stable dedup keys that handle the reality of promotional emails - the same promo is often sent multiple times with slight variations.

**Key Innovation:** The promo_changes table records every significant change, enabling the digest to show NEW/UPDATED badges.

---

## 6.1: Implement Base Key Computation

Type: task
Priority: 1

---

**Goal:** Create compute_base_key() for stable promo deduplication.

**Hierarchy:**
1. **Code** (most stable) - If promo has a code, use it
2. **URL path** (stable) - Normalized URL without query params
3. **Headline hash** (fallback) - MD5 of normalized headline

**Implementation:**
```python
def compute_base_key(code: str | None, landing_url: str | None, headline: str) -> str:
    if code:
        return f"code:{code.upper().strip()}"

    if landing_url:
        normalized = normalize_url(landing_url)
        if normalized:
            return f"url:{normalized}"

    headline_hash = hashlib.md5(normalize_headline(headline).encode()).hexdigest()[:16]
    return f"head:{headline_hash}"
```

**Why This Order:**
- Promo codes are globally unique identifiers
- URLs are stable across email variations
- Headlines may have slight wording changes

**Acceptance Criteria:**
- [ ] Same promo with code always gets same key
- [ ] URL normalization strips query params
- [ ] Headline normalization handles case/whitespace

---

## 6.2: Implement URL Normalization

Type: task
Priority: 1
Deps: 6.1

---

**Goal:** Create normalize_url() for consistent URL comparison.

**Normalization:**
- Extract host + path only
- Remove query parameters
- Remove fragments
- Lowercase host
- Strip trailing slashes

**Example:**
```
Input:  https://nike.com/sale?utm_source=email#top
Output: nike.com/sale
```

**Why Remove Query Params:**
- UTM parameters vary across sends
- Session IDs vary
- The path identifies the promo, not the tracking

**Acceptance Criteria:**
- [ ] Removes query params and fragments
- [ ] Handles various URL formats
- [ ] Returns None for invalid URLs

---

## 6.3: Implement Headline Normalization

Type: task
Priority: 1
Deps: 6.1

---

**Goal:** Create normalize_headline() for consistent comparison.

**Normalization:**
- Lowercase
- Remove extra whitespace
- Remove punctuation (optional)
- Trim

**Example:**
```
Input:  "   25% OFF Everything!  "
Output: "25 off everything"
```

**Acceptance Criteria:**
- [ ] Handles case variations
- [ ] Handles whitespace variations
- [ ] Produces stable hash input

---

## 6.4: Implement Promo Matching

Type: task
Priority: 1
Deps: 6.1, 6.2, 6.3

---

**Goal:** Create find_matching_promo() to find existing canonical promo.

**Matching Logic:**
1. Look for exact base_key match in same store
2. Only match if promo is "recent" (seen in last 30 days OR ending soon OR no end date)
3. Fallback: headline similarity with RapidFuzz (optional)

**Recency Window Logic:**
```python
promo = session.query(Promo).filter(
    Promo.store_id == store_id,
    Promo.base_key == base_key,
    or_(
        Promo.last_seen_at >= window_start,
        Promo.ends_at >= now - timedelta(days=2),
        Promo.ends_at.is_(None),
    )
).first()
```

**Why Recency Window:**
- Old promos with same code might be different campaigns
- But promos ending soon should still match
- Open-ended promos always match

**Acceptance Criteria:**
- [ ] Matches by base_key within recency window
- [ ] Handles edge cases (no end date, just ended)
- [ ] Returns None for genuinely new promos

---

## 6.5: Implement Change Detection

Type: task
Priority: 1
Deps: 6.4

---

**Goal:** Create detect_and_record_changes() to track promo updates.

**Change Types:**
- `created` - New promo
- `end_extended` - End date pushed out
- `discount_changed` - Percent/amount changed
- `code_added` - Code added to codeless promo
- `code_changed` - Code replaced
- `details_updated` - Other field changes

**Implementation:**
```python
def detect_and_record_changes(existing: Promo, candidate: PromoCandidate, email_id):
    changes = []

    if candidate.ends_at:
        new_ends = parse_datetime(candidate.ends_at)
        if existing.ends_at is None or new_ends > existing.ends_at:
            changes.append(("end_extended", {
                "before": existing.ends_at.isoformat() if existing.ends_at else None,
                "after": new_ends.isoformat(),
            }))

    if candidate.percent_off != existing.percent_off:
        changes.append(("discount_changed", {...}))

    if candidate.code and not existing.code:
        changes.append(("code_added", {"code": candidate.code}))

    for change_type, diff_json in changes:
        session.add(PromoChange(...))
```

**Acceptance Criteria:**
- [ ] Detects all change types
- [ ] Records diffs in JSONB for debugging
- [ ] Links to triggering email

---

## 6.6: Implement Promo Merge Logic

Type: task
Priority: 1
Deps: 6.4, 6.5

---

**Goal:** Create merge_extracted_promos() to merge extractions into canonical promos.

**Logic:**
1. Query promo_extractions not yet merged
2. For each extraction's promos:
   - Compute base_key
   - Find matching promo
   - If found: update, detect changes
   - If not found: create new, record "created" change
   - Link email to promo (promo_email_links)
3. Update promo's last_seen_at
4. Return stats

**Merge Fields (prefer newer):**
- ends_at: Take later date
- discount: Take from newer email
- code: Prefer non-null
- landing_url: Prefer non-null
- Always update last_seen_at

**Acceptance Criteria:**
- [ ] Creates new promos correctly
- [ ] Updates existing promos correctly
- [ ] Records changes for digest
- [ ] Links emails as evidence

---

# Phase 7: Digest Generation

Type: epic
Priority: 1
Deps: Phase 6: Promo Deduplication

---

Select promos for the digest and render them into an HTML email.

**Key Innovation:** Only show NEW or UPDATED promos since the last digest - avoid spamming with static content.

---

## 7.1: Implement Digest Promo Selection

Type: task
Priority: 1

---

**Goal:** Create select_digest_promos() to get NEW/UPDATED promos.

**Logic:**
1. Get last successful digest timestamp
2. Query promo_changes since that time
3. Categorize by change_type:
   - "created" → NEW badge
   - Anything else → UPDATED badge
4. Deduplicate (one promo appears once even with multiple changes)
5. Return promos with badges and store names

**Selection SQL (conceptual):**
```sql
SELECT p.*, pc.change_type
FROM promo_changes pc
JOIN promos p ON p.id = pc.promo_id
WHERE pc.changed_at > :last_digest_at
  AND p.status = 'active'
ORDER BY pc.changed_at DESC
```

**Acceptance Criteria:**
- [ ] Only returns promos changed since last digest
- [ ] Assigns correct badges (NEW/UPDATED)
- [ ] Deduplicates multiple changes to same promo

---

## 7.2: Create Digest HTML Template

Type: task
Priority: 1
Deps: 7.1

---

**Goal:** Create digest.html.j2 Jinja2 template for email.

**Template Structure:**
- Clean, mobile-friendly HTML email
- Header with date and promo count
- Sections grouped by store
- Each promo shows:
  - Badge (NEW/UPDATED)
  - Headline
  - Discount text
  - Code (if any)
  - End date (if known)
  - Link to landing page

**Design Considerations:**
- Use inline CSS (email clients strip <style>)
- Keep simple - email rendering is tricky
- Test in multiple clients

**Acceptance Criteria:**
- [ ] Renders cleanly in Gmail
- [ ] Mobile responsive
- [ ] Badges clearly visible
- [ ] Links work

---

## 7.3: Implement Digest Rendering

Type: task
Priority: 1
Deps: 7.2

---

**Goal:** Create generate_digest() to render promos into HTML.

**Implementation:**
```python
def generate_digest():
    promos = select_digest_promos()
    if not promos:
        return None, 0, 0

    # Group by store
    by_store = group_by_store(promos)

    # Render template
    template = env.get_template("digest.html.j2")
    html = template.render(
        date=datetime.now().strftime("%B %d, %Y"),
        stores=by_store,
        promo_count=len(promos),
        store_count=len(by_store),
    )

    return html, len(promos), len(by_store)
```

**Acceptance Criteria:**
- [ ] Returns None if no promos (skip sending)
- [ ] Groups promos by store
- [ ] Renders valid HTML

---

# Phase 8: Email Sending

Type: epic
Priority: 1
Deps: Phase 7: Digest Generation

---

Send the generated digest via SendGrid.

---

## 8.1: Implement SendGrid Client

Type: task
Priority: 1

---

**Goal:** Create sendgrid_client.py with send function.

**Implementation:**
```python
def send_digest_email(html: str) -> tuple[bool, str | None]:
    sg = SendGridAPIClient(settings.sendgrid_api_key)

    message = Mail(
        from_email=settings.sender_email,
        to_emails=settings.recipient_email,
        subject=f"Deal Digest - {datetime.now().strftime('%B %d')}",
        html_content=html,
    )

    try:
        response = sg.send(message)
        return True, response.headers.get("X-Message-Id")
    except Exception as e:
        logger.error(f"SendGrid error: {e}")
        return False, None
```

**Acceptance Criteria:**
- [ ] Sends email via SendGrid
- [ ] Returns message ID on success
- [ ] Handles errors gracefully

---

# Phase 9: Pipeline Orchestration

Type: epic
Priority: 1
Deps: Phase 8: Email Sending

---

Combine all components into the daily pipeline with proper concurrency control and idempotency.

**Key Innovation:** Advisory locks prevent concurrent runs. Unique constraints prevent double-sends.

---

## 9.1: Implement Advisory Lock Functions

Type: task
Priority: 1

---

**Goal:** Create acquire_advisory_lock() and release_advisory_lock().

**Implementation:**
```python
def acquire_advisory_lock(session, lock_name: str) -> bool:
    lock_id = hash(lock_name) % (2**31)
    result = session.execute(
        text("SELECT pg_try_advisory_lock(:id)"),
        {"id": lock_id}
    )
    return result.scalar()

def release_advisory_lock(session, lock_name: str):
    lock_id = hash(lock_name) % (2**31)
    session.execute(
        text("SELECT pg_advisory_unlock(:id)"),
        {"id": lock_id}
    )
```

**Why Advisory Locks:**
- Prevents concurrent pipeline runs
- Session-based (auto-release on disconnect)
- Works across processes

**Acceptance Criteria:**
- [ ] Lock acquired returns True
- [ ] Lock contention returns False
- [ ] Lock released on completion

---

## 9.2: Implement Run Record Management

Type: task
Priority: 1
Deps: 9.1

---

**Goal:** Create functions to manage run records for idempotency.

**Logic:**
- Check if run exists for today (run_type + digest_date_et)
- If exists and sent, skip (already completed)
- If exists and not sent, resume
- If not exists, create new

**UNIQUE Constraint:** `(run_type, digest_date_et)` prevents duplicate runs

**Acceptance Criteria:**
- [ ] Creates run record at start
- [ ] Updates run with stats at end
- [ ] Prevents double-send

---

## 9.3: Implement Daily Pipeline Orchestrator

Type: task
Priority: 1
Deps: 9.1, 9.2

---

**Goal:** Create run_daily_pipeline() that orchestrates everything.

**Steps:**
1. Compute today's date (Eastern Time)
2. Acquire advisory lock (exit if contention)
3. Check/create run record (exit if already sent today)
4. Seed stores from YAML
5. Ingest emails from Gmail
6. Extract promos from pending emails
7. Merge extractions into canonical promos
8. Generate digest HTML
9. If --dry-run: save to file
10. Else: send via SendGrid
11. Update run record with stats
12. Release advisory lock

**Error Handling:**
- Wrap in try/finally to always release lock
- Record errors in run.error_json
- Don't fail entire run for partial failures

**Acceptance Criteria:**
- [ ] Runs all steps in order
- [ ] Handles --dry-run mode
- [ ] Records stats and errors
- [ ] Idempotent (re-run safe)

---

## 9.4: Connect CLI to Pipeline

Type: task
Priority: 1
Deps: 9.3

---

**Goal:** Wire up CLI commands to pipeline functions.

**Commands:**
- `dealintel seed` - Run seed_stores()
- `dealintel gmail-auth` - Run auth flow
- `dealintel run` - Run full pipeline
- `dealintel run --dry-run` - Dry run mode

**Acceptance Criteria:**
- [ ] All commands work
- [ ] Dry run saves preview HTML
- [ ] Errors shown clearly

---

# Phase 10: Scheduling

Type: epic
Priority: 1
Deps: Phase 9: Pipeline Orchestration

---

Set up automated daily execution.

---

## 10.1: Create macOS launchd Configuration

Type: task
Priority: 1

---

**Goal:** Create launchd plist for scheduled execution.

**File:** `~/Library/LaunchAgents/com.dealintel.daily.plist`

**Configuration:**
- Run at 10:00 AM daily
- Working directory: project root
- Log stdout/stderr to logs/

**Commands:**
```bash
launchctl load ~/Library/LaunchAgents/com.dealintel.daily.plist
launchctl list | grep dealintel  # verify
```

**Acceptance Criteria:**
- [ ] Plist created and valid
- [ ] launchctl load succeeds
- [ ] Runs at scheduled time

---

## 10.2: Document Linux cron Alternative

Type: task
Priority: 1

---

**Goal:** Document cron setup for Linux servers.

**Crontab Entry:**
```
0 10 * * * cd /path/to/deal-intel && .venv/bin/dealintel run >> logs/cron.log 2>&1
```

**Acceptance Criteria:**
- [ ] Documentation clear
- [ ] Tested on Linux (if applicable)

---

# Phase 11: Testing

Type: epic
Priority: 1
Deps: Phase 9: Pipeline Orchestration

---

Create tests to ensure system reliability and enable prompt regression testing.

---

## 11.1: Set Up pytest Infrastructure

Type: task
Priority: 1

---

**Goal:** Configure pytest with fixtures for testing.

**Fixtures Needed:**
- Database session (test database)
- Mock Gmail service
- Mock OpenAI client
- Sample email fixtures

**Acceptance Criteria:**
- [ ] pytest runs
- [ ] Fixtures work
- [ ] Test database isolated

---

## 11.2: Create Unit Tests for Core Functions

Type: task
Priority: 1
Deps: 11.1

---

**Goal:** Test individual functions in isolation.

**Test Coverage:**
- normalize_url() edge cases
- compute_base_key() hierarchy
- parse_from_address() formats
- Email matching logic

**Acceptance Criteria:**
- [ ] Core functions tested
- [ ] Edge cases covered

---

## 11.3: Create Golden File Tests for Extraction

Type: task
Priority: 1
Deps: 11.1

---

**Goal:** Test prompt stability with golden file comparisons.

**Approach:**
1. Save sample emails in tests/fixtures/emails/
2. Save expected extractions in tests/golden/
3. Test compares actual vs expected (allow some flexibility)

**Why Golden Files:**
- Detect prompt regressions
- Document expected behavior
- Enable safe prompt iteration

**Acceptance Criteria:**
- [ ] Golden tests for 5+ email types
- [ ] Tests pass with current prompt
- [ ] Easy to update goldens when prompt changes intentionally

---

## 11.4: Create Integration Tests

Type: task
Priority: 1
Deps: 11.2

---

**Goal:** Test end-to-end flows with mocked external services.

**Test Scenarios:**
- Full pipeline with mock Gmail and OpenAI
- Idempotency (run twice, same result)
- Error recovery (one extraction fails)

**Acceptance Criteria:**
- [ ] Integration tests pass
- [ ] Mocking works correctly

---

# Phase 12: Documentation & Runbook

Type: epic
Priority: 1
Deps: Phase 11: Testing

---

Create operational documentation for ongoing maintenance.

---

## 12.1: Create Troubleshooting Runbook

Type: task
Priority: 1

---

**Goal:** Document common issues and solutions.

**Include:**
- Useful SQL queries (unmatched senders, recent runs, recent changes)
- Common problems table (from plan.md)
- Log locations and interpretation

**Acceptance Criteria:**
- [ ] Runbook created
- [ ] SQL queries tested
- [ ] Common issues documented

---

## 12.2: Create README with Quick Start

Type: task
Priority: 1
Deps: 12.1

---

**Goal:** Write README.md with setup instructions.

**Sections:**
- Overview
- Quick Start
- Configuration
- Development
- Troubleshooting link

**Acceptance Criteria:**
- [ ] README complete
- [ ] Quick start works for new dev

---
