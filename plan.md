Agentized Deal Discovery (local weekly, low-maintenance first)
Executive summary

Build a hybrid ingestion system that feeds the existing deals-bot pipeline (Gmail ingest → LLM extract → dedupe → digest) from two complementary channels:

Web discovery via tiered adapters (store-specific ladder):
Sitemap / RSS (Tier 1) → JSON endpoints (Tier 2) → static category/sale pages (Tier 3) → Browser (Tier 4 fallback)

Newsletter subscription agent using a dedicated service inbox, including confirmation handling (double opt-in) via Gmail API + Playwright.

Daily confirmation poller (lightweight) so opt-in links are clicked quickly, separate from the weekly full run.

Key reliability rules:

Prefer sitemaps/RSS because they are designed for discovery and incremental crawling (e.g., lastmod). 
Sitemaps
+1

Poll confirmation emails daily (lightweight) to avoid expired opt-in links.

De-dupe raw signals before LLM using message_id/url/content hash to avoid double-processing.

Use Playwright persistent context with a separate automation profile (do not use your daily Chrome profile). 
Playwright

Capture traces + screenshots for browser actions and route CAPTCHAs/bot checks to a human-assist queue rather than attempting bypass. 
Playwright
+2
Playwright
+2

Architecture overview
Components

Source Adapters (tiered per store/source)

Newsletter Subscription Agent

Service Inbox Processor (Gmail API)

Browser Runner (Playwright persistent context + artifacts)

LLM Extractor + Normalizer (existing deals-bot)

Deduper + Digest Generator (existing deals-bot)

Run Orchestrator (weekly local job)

Daily Confirmation Poller (lightweight inbox/confirmations only)

Human Assist Queue (folder-based)

High-level data flow
Sitemaps/RSS/JSON/Category Pages ──▶ RawSignals/RawDeals ──┐
Newsletter Agent (signups + confirms) ─▶ Service Inbox ────┼──▶ LLM Extract ▶ Dedupe ▶ Digest
Existing Gmail Newsletter Ingest ───────────────────────────┘

Idempotency: de-dupe raw signals across service inbox + existing Gmail ingest before LLM.

Data contract + adapter interface (merged)
Unified raw object

Use one internal representation for any ingestion “document”:

@dataclass
class RawSignal:
    store_id: str
    source_type: str        # "sitemap" | "rss" | "json" | "category" | "browser" | "email"
    url: Optional[str]
    observed_at: datetime
    payload_type: str       # "email" | "html" | "json" | "text"
    payload: str            # raw body, HTML, JSON string, or raw email (may be truncated)
    payload_ref: Optional[str]  # file/blob reference when payload is large
    payload_sha256: Optional[str]
    payload_size_bytes: Optional[int]
    metadata: dict          # headers, http status, crawl depth, etc.


(Equivalent to the competing plan’s “RawDeal”, but generalized to non-deal inputs too.)

Payload storage note:
- Set a size cap for in-DB payloads; if exceeded, store the full body as a compressed file in a blob dir and set payload_ref + payload_sha256.

Source adapter interface (from competing plan, retained)
class SourceAdapter(Protocol):
    async def discover(self) -> list[RawSignal]: ...
    async def health_check(self) -> "SourceStatus": ...
    @property
    def tier(self) -> "SourceTier": ...

class SourceTier(Enum):
    SITEMAP = 1
    RSS = 1
    API = 2
    CATEGORY = 3
    BROWSER = 4


Health check should validate:

endpoint reachable,

parse success (e.g., sitemap XML),

parse success + acceptable result count (allow zero occasionally; alert only after N consecutive zero-result runs).

Tiered adapter ladder (merged)
Tier 1: SitemapAdapter (primary)

Input: sitemap_urls[] or autodiscovered from robots.txt

Parse sitemap indexes and child sitemaps. (Sitemap index files are part of the protocol.) 
Sitemaps
+1

Filter URLs using store-config patterns:

sale_url_patterns regex list (e.g., /sale/, /clearance/, /promo/)

keyword includes (newsletter, subscribe pages for discovery)

Incremental: prefer URLs with recent lastmod.

Tier 1: RSSAdapter (primary, especially for aggregators)

Parse RSS/Atom

Emit entries as RawSignal(payload_type="text" or "html")

Optionally fetch entry landing page HTML.

Slickdeals provides RSS feeds for forums/threads if RSS is enabled. 
Slickdeals

Tier 2: JSONEndpointAdapter

For retailers exposing stable public JSON endpoints (when discovered).

Treat as “best effort”: add only when stable over time.

Tier 3: CategoryPageAdapter (static HTML)

For known “sale” landing pages that are stable URLs but may have dynamic internals.

Fetch HTML with standard HTTP client first; if content is empty due to JS, fall through to BrowserAdapter.

Tier 4: BrowserAdapter (fallback only)

Playwright persistent context, dedicated user_data_dir.

Used for:

heavily JS sale pages,

newsletter signup flows,

confirmation link clicking,

sites with anti-bot requiring manual intervention.

Store configuration schema (YAML source of truth + SQLite state)
Python dataclass (from competing plan, retained and extended)
@dataclass
class StoreConfig:
    store_id: str
    name: str

    # Tiered sources (try in order)
    sitemap_urls: list[str]                 # e.g., ["https://cos.com/sitemap.xml"]
    sale_url_patterns: list[str]            # regex patterns: [r"/sale/", r"/clearance/"]
    rss_feeds: list[str]                    # optional RSS if available
    json_endpoints: list[str]               # hidden API endpoints if discovered
    sale_page_urls: list[str]               # known static sale pages / category pages

    # Browser fallback config
    requires_browser: bool = False
    browser_config: Optional["BrowserConfig"] = None

    # Newsletter config
    newsletter_signup_url: Optional[str] = None
    newsletter_email_subject_patterns: list[str] = field(default_factory=list)

    # Metadata
    category: str = "clothing"              # "clothing" | "flight"
    enabled: bool = True
    tos_url: Optional[str] = None
    robots_policy: Optional[str] = None     # notes or per-store rules
    crawl_delay_seconds: Optional[int] = None
    max_requests_per_run: Optional[int] = None
    requires_login: bool = False
    allow_login: bool = False
    notes: Optional[str] = None

YAML store config (from my plan, retained concept)

Represent store config in YAML for easy editing; sync into SQLite for runtime state:

stores:
  - id: lululemon
    category: clothing
    sources:
      - type: sitemap
        url: "https://shop.lululemon.com/sitemap.xml"
        include: ["/we-made-too-much/"]
        max_urls: 50
      - type: category
        url: "https://shop.lululemon.com/c/we-made-too-much/n18mhd"
      - type: newsletter
        signup_discovery: "sitemap"
        expected_confirm: false
    tos_url: "https://shop.lululemon.com/terms"
    robots_policy: "respect robots; avoid checkout/account paths"
    crawl_delay_seconds: 2
    max_requests_per_run: 200
    requires_login: false
    allow_login: false
    notes: "Prefer sitemap + WMTM category; avoid heavy JS paths"

Config source of truth (decision)

YAML is the source of truth for store config; SQLite stores runtime state only.

Add a one-way sync command (e.g., sync-stores):
- Load YAML -> upsert into source_configs.config_json
- Log a diff summary (added/removed/changed stores)
- Never edit YAML from SQLite

Newsletter subscription agent (merged)
Key idea

Newsletter signup automation is a stateful workflow:

Submit signup form via Playwright

Confirm via inbox link if double opt-in

Track per-store status in SQLite

Route CAPTCHAs to human queue

Architecture (from competing plan, retained)
┌──────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  Subscription    │     │   Service       │     │  Confirmation    │
│  Initiator       │────▶│   Inbox         │────▶│  Handler         │
│  (Playwright)    │     │   (Gmail API)   │     │  (Link clicker)  │
└──────────────────┘     └─────────────────┘     └──────────────────┘
         │                       │                        │
         ▼                       ▼                        ▼
┌──────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  Status Tracker  │◀────│  Email Parser   │◀────│  Retry Queue     │
│  (SQLite)        │     │ (patterns+LLM)  │     │  (Unconfirmed)   │
└──────────────────┘     └─────────────────┘     └──────────────────┘

State machine (from my plan, retained)

Per store/newsletter:

DISCOVERED_SIGNUP_URL

SIGNUP_SUBMITTED

AWAITING_CONFIRMATION_EMAIL

CONFIRMATION_CLICKED

SUBSCRIBED_CONFIRMED

FAILED_NEEDS_HUMAN

PAUSED

Agent class skeleton (from competing plan, retained)
class NewsletterAgent:
    async def subscribe(self, store: StoreConfig) -> "SubscriptionResult":
        """
        1. Navigate to signup URL (Playwright)
        2. Fill email form with service inbox address
        3. Handle CAPTCHAs (human-in-the-loop queue if needed)
        4. Submit form
        5. Wait for confirmation email (poll inbox)
        6. Extract confirmation link from email
        7. Click confirmation link
        8. Update subscription status
        """

    async def poll_confirmations(self):
        """Check service inbox for pending confirmations."""

    async def handle_captcha(self, screenshot: bytes) -> str:
        """Human-in-the-loop CAPTCHA solving (low-maintenance friendly)."""

Confirmation email parsing (merged)

Pattern-first + LLM fallback (keep competing plan details):

CONFIRMATION_PATTERNS = [
    r"confirm.*subscription",
    r"verify.*email",
    r"activate.*newsletter",
    r"click.*to.*confirm",
]


Extraction strategy:

Parse email HTML, find links/buttons containing confirmation keywords.

Fall back to LLM extraction if patterns fail.

Validate URL domain matches expected sender / allowlist.

Inbox processing implementation details (from my plan, retained + cited)

Use Gmail API users.messages.list to search/poll the service inbox. 
Google for Developers

Track Gmail historyId and processed messageId to avoid reprocessing and to catch incremental changes.

Use users.messages.get with format=METADATA and metadataHeaders[] to fetch only key headers cheaply (From, Subject, List-ID, List-Unsubscribe) before downloading full bodies. 
Google for Developers

Use List-Unsubscribe headers to help:

classify “this is a real mailing list message”

and optionally support cleanup/unsubscribe flows later (RFC 2369 / RFC 8058). 
IETF
+1

Why not use Gmail forwarding APIs (retain my detail)

Creating forwarding addresses via Gmail API can require verification and is restricted to service accounts with domain-wide delegation—poor fit for local MVP. 
Google for Developers

Browser automation setup (merged, with guardrails)
Dedicated profile requirement (retain)

Playwright explicitly warns against pointing userDataDir at your regular Chrome “User Data” directory; use a separate directory for automation. 
Playwright

BrowserConfig (from competing plan, retained but re-scoped)
BROWSER_CONFIG = {
    "user_data_dir": "~/.deals-bot/chrome-profile",
    "headless": False,  # start headed for debugging; switch later
    "args": [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
    ],
    "stealth": False,  # off by default; enable only per-store if needed
}


Important guardrail on “stealth”:

Treat stealth as experimental and last-resort; enable only per-store after manual review. Python playwright-stealth warns it won’t bypass more than the simplest detection methods. 
PyPI

Prefer human-in-the-loop over evasion attempts when CAPTCHAs occur.

Tracing + screenshots (retain + cite)

Always enable tracing on browser workflows; traces are designed for post-mortem debugging via Trace Viewer. 
Playwright
+1

Extensions caveat (retain my detail + cite)

Avoid relying on Chrome extensions for automation reliability:

Extensions only work in Chromium with persistent context; custom args can break Playwright.

Branded Chrome/Edge removed flags for sideloading extensions; Playwright docs recommend using bundled Chromium for extensions. 
Playwright

Daily confirmation poller (lightweight)
class DailyConfirmations:
    async def run(self):
        # Inbox-only, no LLM/dedupe/digest
        await self.newsletter_agent.poll_confirmations()
        await self.newsletter_agent.retry_failed_subscriptions()
        await self.service_inbox_processor.update_history()

Weekly orchestration (from competing plan, retained)
class WeeklyPipeline:
    async def run(self):
        # 1. Check newsletter subscription health
        await self.newsletter_agent.poll_confirmations()
        await self.newsletter_agent.retry_failed_subscriptions()

        # 2. Run source adapters in tier order
        raw_deals: list[RawSignal] = []
        for store in self.stores:
            for adapter in self.get_adapters_by_tier(store):
                try:
                    signals = await adapter.discover()
                    raw_deals.extend(signals)
                    break  # Success, skip lower tiers
                except AdapterError as e:
                    self.log_failure(store, adapter, e)
                    continue  # Try next tier

        # 3. Ingest newsletter emails (existing deals-bot ingest + service inbox)
        email_deals = await self.ingest_newsletter_emails()
        raw_deals.extend(email_deals)

        # 3.5 Idempotency: de-dupe raw signals before LLM
        # Keys: message_id (email), normalized_url, payload_sha256
        raw_deals = await self.raw_signal_deduper.filter(raw_deals)

        # 4. LLM extraction + normalization (existing)
        normalized = await self.llm_extractor.process(raw_deals)

        # 5. Dedupe (existing)
        new_deals = await self.deduplicator.filter_new(normalized)

        # 6. Store and generate digest (existing)
        await self.store_deals(new_deals)
        await self.generate_digest(new_deals)

Error handling + human-in-the-loop triage (merged)
Failure handler (from competing plan, retained)
class FailureHandler:
    async def on_browser_failure(self, store: str, error: Exception, page):
        screenshot_path = f"failures/{store}_{timestamp()}.png"
        await page.screenshot(path=screenshot_path)

        trace_path = f"failures/{store}_{timestamp()}.zip"
        await page.context.tracing.stop(path=trace_path)

        self.failure_queue.append({
            "store": store,
            "error": str(error),
            "screenshot": screenshot_path,
            "trace": trace_path,
            "timestamp": datetime.now(),
            "requires_human": self.is_captcha(error),
        })

HumanAssistQueue (from competing plan, retained + my “task folder” model)
class HumanAssistQueue:
    QUEUE_DIR = "~/.deals-bot/human-assist/"

    async def enqueue_captcha(self, screenshot: bytes, context: dict):
        task_id = uuid4()
        task_path = Path(self.QUEUE_DIR) / f"{task_id}"
        task_path.mkdir(parents=True)

        (task_path / "captcha.png").write_bytes(screenshot)
        (task_path / "context.json").write_text(json.dumps(context))
        (task_path / "solution.txt")  # human writes solution here

        return await self.wait_for_solution(task_id, timeout=3600)

Retention:
- Auto-delete resolved tasks and artifacts after N days (configurable) to limit PII/token exposure.

Also save:

HTML snapshot (page.content()) on failures (retain my detail).

Optional CLI: python -m deals_bot.triage open --task <id> to open trace viewer and artifacts.

Database / schema updates (from competing plan, retained + aligned)

Keep store configs mostly static; track runs + subscription state in SQLite.

Tables (retain competing plan verbatim)
CREATE TABLE source_configs (
    id TEXT PRIMARY KEY,
    store_name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    last_successful_run TIMESTAMP,
    failure_count INTEGER DEFAULT 0,
    enabled BOOLEAN DEFAULT TRUE
);

CREATE TABLE newsletter_subscriptions (
    id TEXT PRIMARY KEY,
    store_id TEXT REFERENCES source_configs(id),
    email_address TEXT NOT NULL,
    status TEXT NOT NULL,  -- 'pending' | 'confirmed' | 'failed'
    subscribed_at TIMESTAMP,
    confirmed_at TIMESTAMP,
    last_email_received TIMESTAMP
);

CREATE TABLE ingestion_runs (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    deals_discovered INTEGER,
    deals_new INTEGER,
    failures_json TEXT
);

Add runtime-state tables (optional but recommended):

CREATE TABLE inbox_state (
    id TEXT PRIMARY KEY,
    gmail_history_id TEXT,
    last_checked_at TIMESTAMP
);

Store processed Gmail message IDs (or use a unique index on message_id) for idempotency.

CREATE TABLE raw_signal_blobs (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL
);


Add (optional) fields from my state machine by either:

extending newsletter_subscriptions.status to include:

awaiting_confirmation, failed_needs_human, paused

or adding a second column state that maps to the richer state machine.

Initial source coverage (merged)
Clothing: initial store list (from competing plan, retained)

COS — Primary: Sitemap + Sale page; Newsletter: Yes; Notes: clean sitemap structure

Nike — Primary: Sitemap; Fallback: Browser; Newsletter: Yes; Notes: heavy JS, may need browser

Lululemon — Primary: Sale page; Fallback: Browser; Newsletter: Yes; Notes: good email deals

Corridor — Primary: Sale page; Newsletter: Yes; Notes: smaller brand, simpler site

J.Crew — Primary: Sitemap; Newsletter: Yes; Notes: good sale section

Uniqlo — Primary: Sale page; Fallback: Browser; Newsletter: Yes; Notes: frequent sales

Patagonia — Primary: Sitemap; Newsletter: Yes; Notes: Web Specials page

Everlane — Primary: Sale page; Newsletter: Yes; Notes: “Choose What You Pay”

Bonobos — Primary: Sitemap; Newsletter: Yes; Notes: clear sale section

Todd Snyder — Primary: Sale page; Newsletter: Yes; Notes: good newsletter deals

Deal aggregators (merged)

DealNews clothing RSS (candidate feed from competing plan)

Slickdeals RSS (candidate apparel feed link from competing plan)

Secret Flying RSS feed (candidate)

The Flight Deal RSS feed (candidate)

Brad’s Deals (retain from my plan; add RSS/HTML ingestion as available)

Candidate feed URLs (retain competing plan verbatim; treat as “to validate”):

DEAL_AGGREGATOR_FEEDS = {
  "slickdeals_apparel": "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1&forumchoice%5B%5D=9",
  "dealnews_clothing": "https://www.dealnews.com/c196/Clothing/rss.xml",
  "secret_flying": "https://www.secretflying.com/feed/",
  "the_flight_deal": "https://www.theflightdeal.com/feed/",
}

Flights: sources (from competing plan + my plan, retained)

Secret Flying — RSS + Newsletter — Free

The Flight Deal — RSS — Free

Going (Scott’s Cheap Flights) — Newsletter — free tier limited

Airline newsletters — Delta Deals, United deals — Newsletter — Free

Airfarewatchdog — price alerts workflow (retain my plan)

Travelzoo Top 20 — weekly curated list (retain my plan)

FlyerTalk — Forum RSS (retain competing plan)

Flight newsletter strategy (from competing plan, retained):

Subscribe service inbox to Delta Deals, United deals, Secret Flying, Going (free tier)

Parse emails for route + price extraction

Avoid paid flight APIs for MVP

Flight API stance (merged, cited and scoped)

Google Flights integration docs are explicitly “invite-only partner specs” shared under NDA. 
Google for Developers

Amadeus production access has explicit “moving to production” requirements. 
Amadeus IT Group SA

If you later want structured search/booking, commercial APIs like Duffel exist, but they expand scope. 
Duffel
+1

Compliance / ToS / legal posture (merged, with corrected nuance)

Robots.txt compliance: it’s advisory guidance for crawlers, not authorization. You still need to respect site ToS and be rate-limited/gentle. 
RFC Editor

Add per-store ToS URL + crawl policy fields in config; enforce a shared rate limiter and store-level max_requests_per_run.

Newsletter automation: subscribing your own service inbox is consent-based. If you later send a digest email as a “commercial email,” CAN‑SPAM applies (opt-out expectations, etc.). 
Federal Trade Commission
+1

Scraping case law: Meta v Bright Data and hiQ v LinkedIn provide useful context, but they are not blanket permission for all scraping everywhere. 
Quinn Emanuel
+2
Lowenstein Sandler
+2

Current enforcement risk exists: avoid circumvention tactics and high-volume scraping. Google’s Dec 19, 2025 SerpApi lawsuit highlights this risk profile. 
Reuters
+1

Milestones / rollout plan (merged)

This merges my Phase 0–5 with the competing plan’s Week 1–5 schedule.

Week 1: Foundation (Phase 0)

Deliverables:

RawSignal data contract

SourceAdapter interface + tier enum

source_configs persistence

YAML -> SQLite sync command for store configs (one-way)

Basic end-to-end path: one dummy adapter → LLM extract → dedupe → digest

Week 2: Low-maintenance sources (Phase 1)

Deliverables:

SitemapAdapter (index + lastmod incremental)

RSSAdapter

Configure initial aggregator feeds + 2–3 store sitemaps

Health checks + basic run logging

Week 3: Browser runner + triage (Phase 2)

Deliverables:

Playwright persistent context wrapper with dedicated user_data_dir 
Playwright

Trace + screenshot capture on failures 
Playwright
+1

HumanAssistQueue folder + CLI helper

Week 4: Newsletter agent (Phase 3)

Deliverables:

NewsletterAgent.subscribe() + confirmation polling

Gmail metadata-first inbox parsing 
Google for Developers

Store-specific subject patterns + confirmation regex patterns

Subscription status tracking in newsletter_subscriptions

Daily confirmation poller job + historyId/messageId tracking

Week 5: Integration, polish, validation (Phase 4)

Deliverables:

Configure ~10 clothing stores (list above)

Configure 4+ flight sources (list above)

Two weekly runs with:

ingestion_runs metrics

failure review loop

extraction quality checks

Later (Phase 5 optional)

Proxy rotation (only if blocking becomes real)

Paid flight APIs (only if newsletters/RSS aren’t sufficient)

Inbound parsing service (Mailgun/Postmark) if Gmail becomes a bottleneck

Risks & mitigations (merged)

CAPTCHAs on signup (High likelihood / Medium impact)
Mitigation: human-in-the-loop queue; usually one-time per store; avoid bypass. 
PyPI

Newsletter format changes (Medium / Low)
Mitigation: pattern matching first; LLM fallback; monitor extraction quality.

Config drift between YAML and SQLite (Medium / Low)
Mitigation: YAML is source of truth; one-way sync with diff logs; avoid editing SQLite for config.

Payload bloat in SQLite (Medium / Medium)
Mitigation: size cap + external blob storage; store payload_ref + hash in DB.

Sitemap structure changes / empty results (Low / Low)
Mitigation: regex filtering + alerts on “0 results”; fall back to CategoryPageAdapter.

IP blocking (Low / Medium)
Mitigation: weekly cadence is gentle; consider residential proxy only if needed.

ToS / legal enforcement risk (Low–Medium / Potentially High)
Mitigation: stay logged-off only where appropriate, avoid circumventing controls, respect robots rules, rate limit, prefer newsletters/RSS. Recent SerpApi litigation reinforces avoidance of evasion tactics. 
Reuters
+2
blog.google
+2

Alternatives considered (merged; keep all)

Paid CAPTCHA solving (2Captcha)
Competing plan notes $1–3 per 1000 challenges; for weekly cadence and ~20 stores, human-in-loop is cheaper and CAPTCHAs are often one-time.

Flight API-first (Amadeus / others)
Competing plan: onerous production process; not needed for MVP.
Merged nuance: Amadeus has explicit production requirements; Google Flights specs are invite-only. 
Amadeus IT Group SA
+1

Aggregator-first
Very low maintenance and broad coverage; tradeoff is less “brand-direct” and sometimes noisier deals.

Inbound email parsing service instead of Gmail (my plan)
Pros: easier programmatic control, catch-all, simpler multi-tenant future.
Cons: external dependency/cost; still need confirmation-click automation.

Browser-first
Works but fragile/slow; contradicts “low maintenance first.”

Prioritization (merged)

Build first:

Sitemap adapter (highest reliability)

RSS adapter for aggregators + flights

BrowserRunner with traces/screenshots

Newsletter subscription agent (signup + confirmation + status tracking)

Build second:
5) CategoryPageAdapter
6) JSONEndpointAdapter (only when stable endpoints discovered)
7) HumanAssistQueue polish + triage tooling

Build last (only if necessary):
8) Proxy rotation
9) Paid flight APIs / structured flight search
