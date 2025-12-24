"""Tests for sale page parsing."""

from dealintel.web.parse_sale import parse_sale_page

SALE_HTML = """
<html>
  <body>
    <div class="product-card">
      <h3>Jacket</h3>
      <span class="price price--original">$200</span>
      <span class="price price--sale">$120</span>
    </div>
    <div class="product-card">
      <h3>Shirt</h3>
      <s>$100</s>
      <span class="price">$70</span>
    </div>
  </body>
</html>
"""


def test_sale_page_extracts_prices():
    summary = parse_sale_page(SALE_HTML, "https://example.com/sale")
    samples = {sample.name: sample for sample in summary.product_samples}

    assert samples["Jacket"].original_price == 200.0
    assert samples["Jacket"].sale_price == 120.0
    assert samples["Jacket"].discount_percent == 40

    assert samples["Shirt"].original_price == 100.0
    assert samples["Shirt"].sale_price == 70.0
    assert samples["Shirt"].discount_percent == 30

    assert summary.discount_range == (30, 40)
