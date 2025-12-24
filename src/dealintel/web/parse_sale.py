"""Specialized parser for e-commerce sale pages."""

import re
from dataclasses import dataclass

import structlog
from bs4 import BeautifulSoup, Tag

logger = structlog.get_logger()
PRICE_PATTERN = re.compile(r"\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)")


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

            original_price, sale_price = _extract_prices(product)
            discount_percent = _compute_discount_percent(original_price, sale_price)

            samples.append(
                ProductSample(
                    name=name,
                    original_price=original_price,
                    sale_price=sale_price,
                    discount_percent=discount_percent,
                )
            )
        except Exception:
            logger.debug("Failed to parse product tile", exc_info=True)
            continue

    return samples


def _extract_prices(product: Tag) -> tuple[float | None, float | None]:
    """Extract original and sale prices from product tile text/classes."""
    original_candidates: list[float] = []
    sale_candidates: list[float] = []
    all_prices: list[float] = []

    price_elements = product.select(
        "[class*='price'], [class*='Price'], [class*='amount'], .price, s, del"
    )

    for element in price_elements:
        text = element.get_text(" ", strip=True)
        if not text:
            continue

        prices = _parse_prices(text)
        if not prices:
            continue

        all_prices.extend(prices)

        class_attr = " ".join(element.get("class") or []).lower()
        if element.name in {"s", "del"} or any(token in class_attr for token in ("original", "compare", "was", "old")):
            original_candidates.extend(prices)
        elif any(token in class_attr for token in ("sale", "current", "now", "discount")):
            sale_candidates.extend(prices)

    if original_candidates or sale_candidates:
        original = max(original_candidates) if original_candidates else None
        sale = min(sale_candidates) if sale_candidates else None
        if sale is None and original is not None and len(all_prices) >= 2:
            sale = min(all_prices)
        if original is None and sale is not None and len(all_prices) >= 2:
            original = max(all_prices)
    else:
        if len(all_prices) >= 2:
            original = max(all_prices)
            sale = min(all_prices)
        elif len(all_prices) == 1:
            original = None
            sale = all_prices[0]
        else:
            return None, None

    if original is not None and sale is not None and sale > original:
        original, sale = sale, original

    return original, sale


def _parse_prices(text: str) -> list[float]:
    matches = PRICE_PATTERN.findall(text)
    prices: list[float] = []
    for match in matches:
        normalized = match.replace(",", "")
        try:
            prices.append(float(normalized))
        except ValueError:
            continue
    return prices


def _compute_discount_percent(original: float | None, sale: float | None) -> int | None:
    if original is None or sale is None or original <= 0:
        return None
    if sale > original:
        return None
    return int(round((1 - (sale / original)) * 100))
