"""Tests for RSS/Atom feed parsing."""

from dealintel.web.parse_feed import is_feed_content, parse_rss_feed

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <title>Secret Flying</title>
    <item>
      <title>San Francisco to Paris for $499</title>
      <link>https://secretflying.com/posts/sfo-to-paris-499/</link>
      <description>Sample deal description</description>
      <pubDate>Mon, 20 Dec 2024 10:00:00 -0500</pubDate>
    </item>
  </channel>
</rss>
"""


def test_is_feed_content():
    assert is_feed_content(RSS_SAMPLE, "https://secretflying.com/feed/")


def test_parse_rss_feed():
    entries = parse_rss_feed(RSS_SAMPLE)
    assert len(entries) == 1

    entry = entries[0]
    assert entry.title == "San Francisco to Paris for $499"
    assert entry.link == "https://secretflying.com/posts/sfo-to-paris-499/"
    assert "Sample deal description" in entry.summary
    assert entry.published_at is not None
