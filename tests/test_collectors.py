from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from app.collectors.normalize import canonical_url, clean_text, normalize_date, validate_public_url
from app.collectors.feeds import parse_html, parse_rss
from app.selection.ranking import same_event, score_event

RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Example</title>
<item><title>Space agency launches new moon mission</title><link>https://example.com/news?utm_source=rss</link>
<description>&lt;p&gt;A detailed report about the latest moon mission and the spacecraft carrying scientific instruments.&lt;/p&gt;</description>
<pubDate>Tue, 22 Sep 2026 08:00:00 +0700</pubDate></item>
<item><title>Missing URL</title></item></channel></rss>'''


def test_url_normalization():
    assert canonical_url('HTTPS://Example.COM:443/a?utm_source=rss&b=2&a=1#top') == 'https://example.com/a?a=1&b=2'
    assert canonical_url('/b', 'https://example.com/a') == 'https://example.com/b'
    for url in ('javascript:alert(1)', 'file:///etc/passwd', 'https://user:pass@example.com'):
        with pytest.raises(ValueError):
            canonical_url(url)


def test_private_sources_rejected():
    with pytest.raises(ValueError, match='công khai'):
        validate_public_url('http://127.0.0.1/feed')


def test_clean_and_parse_rss():
    articles, errors = parse_rss(RSS, 'https://example.com/feed')
    assert errors == 1 and len(articles) == 1
    assert articles[0].canonical_url == 'https://example.com/news'
    assert articles[0].published_at == datetime(2026, 9, 22, 1, tzinfo=timezone.utc)
    assert '<p>' not in articles[0].summary
    assert clean_text('<script>bad()</script> Hello <b>world</b>') == 'Hello world'


def test_invalid_feed_and_html_adapter():
    with pytest.raises(ValueError):
        parse_rss(b'<html>Not RSS</html>', 'https://example.com')
    items, errors = parse_html(b'<article><h2><a href="/story">Title</a></h2><p>Text</p><time datetime="2026-09-20T01:00:00Z"></time></article>', 'https://example.com', 'generic')
    assert not errors and items[0].canonical_url == 'https://example.com/story'


def test_ranking_components():
    now = datetime.now(timezone.utc)
    article = SimpleNamespace(title='Space mission', summary='x' * 100, canonical_url='https://example.com', topic='Science', published_at=now, collected_at=now, source_id=1)
    score, parts, reasons = score_event([article], ['space'], now)
    assert score == 80 and score == sum(parts.values()) and len(reasons) == 4
    assert same_event(article, article)
    assert normalize_date(datetime(2100, 1, 1, tzinfo=timezone.utc)) is None
