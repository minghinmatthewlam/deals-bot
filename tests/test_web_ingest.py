"""Tests for web ingestion (no network calls)."""

from dealintel.web.ingest import _web_message_id
from dealintel.web.parse import parse_web_html

COS_SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Sale | COS</title></head>
<body>
    <h1>End of Season Sale</h1>
    <p>Up to 50% off selected items</p>
</body>
</html>
"""


class TestWebMessageId:
    def test_deterministic(self):
        id1 = _web_message_id("https://example.com", "abc123")
        id2 = _web_message_id("https://example.com", "abc123")
        assert id1 == id2

    def test_format(self):
        msg_id = _web_message_id("https://example.com", "abcdef123456")
        assert msg_id.startswith("web:")
        parts = msg_id.split(":")
        assert len(parts) == 3


class TestParseWebHtml:
    def test_extracts_title(self):
        parsed = parse_web_html(COS_SAMPLE_HTML)
        assert parsed.title == "Sale | COS"

    def test_extracts_body_text(self):
        parsed = parse_web_html(COS_SAMPLE_HTML)
        assert "End of Season Sale" in parsed.body_text
        assert "50% off" in parsed.body_text
