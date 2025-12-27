"""Tests for sale page parsing."""

from dealintel.web.parse_sale import parse_sale_page


def test_parse_sale_page_attribute_prices():
    html = """
    <html>
      <head><title>Big Sale</title></head>
      <body>
        <div class="product-card" data-compare-at-price="12900" data-price="9900">
          <h3>Widget Jacket</h3>
        </div>
      </body>
    </html>
    """

    summary = parse_sale_page(html, "https://example.com/sale")
    assert summary.product_samples

    sample = summary.product_samples[0]
    assert sample.name == "Widget Jacket"
    assert sample.original_price == 129.0
    assert sample.sale_price == 99.0
    assert sample.discount_percent == 23
