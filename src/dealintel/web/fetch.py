"""HTTP fetching with retries and caching headers."""

from dataclasses import dataclass

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

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


def _is_retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return _is_retryable_http_status(exc.response.status_code)
    return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
    retry=retry_if_exception(_should_retry),
    reraise=True,
)
def fetch_url(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    etag: str | None = None,
    last_modified: str | None = None,
    max_content_length: int | None = None,
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

            if response.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )

            content = response.text
            truncated = False
            limit = MAX_CONTENT_LENGTH if max_content_length is None else max_content_length
            if limit and len(content) > limit:
                content = content[:limit] + "\n\n[TRUNCATED]"
                truncated = True
                logger.warning("Content truncated", url=url, limit_bytes=limit)

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
        status_code = e.response.status_code
        if _is_retryable_http_status(status_code):
            logger.warning("Retryable HTTP error", url=url, status=status_code)
            raise
        logger.warning("HTTP error (non-retryable)", url=url, status=status_code)
        return FetchResult(
            final_url=str(e.response.url),
            status_code=status_code,
            text=None,
            error=f"HTTP {status_code}",
        )
    except httpx.RequestError as e:
        logger.error("Request error", url=url, error=str(e))
        return FetchResult(
            final_url=url,
            status_code=0,
            text=None,
            error=str(e),
        )
