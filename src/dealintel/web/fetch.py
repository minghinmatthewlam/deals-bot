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
