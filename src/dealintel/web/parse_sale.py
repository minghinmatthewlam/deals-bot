"""Specialized parser for e-commerce sale pages."""

from dataclasses import dataclass

import structlog
from bs4 import BeautifulSoup, Tag

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

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else None
    banners = _extract_banner_text(soup)
    products = _sample_products(soup, limit=10)

    discounts = [p.discount_percent for p in products if p.discount_percent is not None]
    discount_range = (min(discounts), max(discounts)) if discounts else None

    categories: list[str] = []
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
        for product in summary.product_samples:
            parts = [f"  - {product.name}"]
            if product.original_price is not None and product.sale_price is not None:
                parts.append(f": ${product.original_price:.0f} → ${product.sale_price:.0f}")
            if product.discount_percent is not None:
                parts.append(f" ({product.discount_percent}% off)")
            lines.append("".join(parts))
        lines.append("")

    if summary.discount_range:
        min_discount, max_discount = summary.discount_range
        lines.append(f"Observed Discount Range: {min_discount}% - {max_discount}% off")

    return "\n".join(lines)


def _extract_banner_text(soup: BeautifulSoup) -> list[str]:
    """Extract prominent banner/hero text."""
    banners: list[str] = []
    selectors = ["h1", ".hero-title", ".banner-title", "[class*='hero']"]

    for selector in selectors:
        for element in soup.select(selector)[:3]:
            text = element.get_text(strip=True)
            if text and len(text) < 200:
                banners.append(text)

    return list(dict.fromkeys(banners))[:5]


def _sample_products(soup: BeautifulSoup, limit: int = 10) -> list[ProductSample]:
    """Sample product names from sale page tiles."""
    samples: list[ProductSample] = []

    product_selectors = [
        "[class*='product-tile']",
        "[class*='product-card']",
        ".product",
    ]

    products: list[Tag] = []
    for selector in product_selectors:
        products = soup.select(selector)
        if products:
            break

    for product in products[:limit]:
        try:
            name_el = product.select_one("[class*='name'], [class*='title'], h2, h3")
            name = name_el.get_text(strip=True) if name_el else None

            if not name or len(name) > 100:
                continue

            samples.append(
                ProductSample(
                    name=name,
                    original_price=None,
                    sale_price=None,
                    discount_percent=None,
                )
            )
        except Exception:
            logger.debug("Failed to parse product tile", exc_info=True)
            continue

    return samples
