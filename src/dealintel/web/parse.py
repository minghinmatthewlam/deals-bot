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
    if canonical:
        href = canonical.get("href")
        if isinstance(href, str):
            return href
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
