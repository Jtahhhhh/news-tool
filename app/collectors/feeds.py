import calendar
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin
import feedparser
import httpx
from bs4 import BeautifulSoup
from app.config import get_settings
from app.collectors.normalize import canonical_url, clean_text, fingerprint, normalize_date, validate_public_url


@dataclass
class CollectedArticle:
    canonical_url: str
    title: str
    summary: str
    published_at: datetime | None
    fingerprint: str


def fetch(url: str) -> tuple[bytes, str]:
    settings = get_settings()
    with httpx.Client(timeout=settings.request_timeout_seconds, follow_redirects=False,
                      headers={'User-Agent': 'NewsTool/0.1 (RSS reader)'}, trust_env=False) as client:
        for _ in range(6):
            url = validate_public_url(url, settings.allow_private_sources)
            with client.stream('GET', url) as response:
                if response.is_redirect:
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('Redirect thiếu Location')
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > settings.max_response_bytes:
                        raise ValueError('Nội dung vượt MAX_RESPONSE_BYTES')
                    chunks.append(chunk)
                return b''.join(chunks), url
    raise ValueError('Nguồn redirect quá nhiều lần')


def make_article(title, url, summary, published, base):
    title = clean_text(title)[:1000]
    if not title or not url:
        raise ValueError('Bài thiếu tiêu đề hoặc URL')
    published = normalize_date(published)
    return CollectedArticle(canonical_url(url, base), title, clean_text(summary)[:4000],
                            published, fingerprint(title, published))


def parse_rss(body: bytes, base: str):
    feed = feedparser.parse(body)
    if not feed.entries and (feed.bozo or not feed.version):
        raise ValueError('Nội dung không phải RSS/Atom hợp lệ')
    articles, errors = [], 0
    for entry in feed.entries[:get_settings().max_articles_per_job]:
        try:
            date = entry.get('published_parsed') or entry.get('updated_parsed')
            published = datetime.fromtimestamp(calendar.timegm(date), timezone.utc) if date else None
            articles.append(make_article(entry.get('title', ''), entry.get('link', ''),
                                         entry.get('summary', ''), published, base))
        except (ValueError, TypeError, OverflowError):
            errors += 1
    return articles, errors


# Source adapters only extract listing metadata; no article-page requests.
HTML_ADAPTERS = {
    'generic': ('article', 'h2 a, h3 a', 'p', 'time'),
    'vnexpress': ('article.item-news', 'h3.title-news a', 'p.description', 'time'),
}


def parse_html(body: bytes, base: str, adapter: str):
    if adapter not in HTML_ADAPTERS:
        raise ValueError(f'Adapter HTML chưa được hỗ trợ: {adapter}')
    item_css, link_css, summary_css, date_css = HTML_ADAPTERS[adapter]
    soup = BeautifulSoup(body, 'html.parser')
    items = soup.select(item_css)
    if not items:
        raise ValueError('Adapter không tìm thấy bài; kiểm tra cấu trúc HTML của nguồn')
    articles, errors = [], 0
    for item in items[:get_settings().max_articles_per_job]:
        try:
            link, summary, date = item.select_one(link_css), item.select_one(summary_css), item.select_one(date_css)
            published = None
            if date and date.get('datetime'):
                try:
                    published = datetime.fromisoformat(date['datetime'].replace('Z', '+00:00'))
                except ValueError:
                    pass
            articles.append(make_article(link.get_text(' ') if link else '', link.get('href', '') if link else '',
                                         summary.get_text(' ') if summary else '', published, base))
        except (ValueError, TypeError, OverflowError):
            errors += 1
    return articles, errors


def collect(source):
    body, base = fetch(source.url)
    return parse_rss(body, base) if source.kind == 'rss' else parse_html(body, base, source.adapter)
