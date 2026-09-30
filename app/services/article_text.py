"""Best-effort article extraction. Failure never expands the RSS evidence."""
from urllib.parse import urljoin
import time
import httpx
from bs4 import BeautifulSoup
from app.collectors.normalize import clean_text, validate_public_url
from app.config import get_settings


def extract_article(html):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.select('script,style,nav,footer,aside,form,iframe,noscript,header,.advertisement,.ads,.related,.comments'):
        tag.decompose()
    node = soup.select_one('article, [itemprop="articleBody"], .article-content, .detail-content, main')
    if node is None:
        raise ValueError('article_body_missing')
    text = clean_text(node.get_text(' ', strip=True))
    if len(text) < 80 or len(text.split()) < 12:
        raise ValueError('article_body_too_short')
    return text[:20000]


def fetch_article(url, *, client=None):
    settings = get_settings()
    owned = client is None
    client = client or httpx.Client(follow_redirects=False, trust_env=False)
    deadline = time.monotonic() + 9
    try:
        for _ in range(3):
            if time.monotonic() >= deadline:
                raise ValueError('article_timeout')
            url = validate_public_url(url)  # Revalidate every redirect, including private destinations.
            with client.stream('GET', url, timeout=settings.article_fetch_timeout_seconds,
                               headers={'User-Agent': 'NewsTool/1.0 article reader'}) as response:
                if response.status_code in (301,302,303,307,308):
                    url = urljoin(url, response.headers['location'])
                    continue
                response.raise_for_status()
                if 'html' not in response.headers.get('content-type', '').lower():
                    raise ValueError('not_html')
                content = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise ValueError('article_timeout')
                    content.extend(chunk)
                    if len(content) > 2_000_000:
                        raise ValueError('article_too_large')
                return extract_article(bytes(content))
        raise ValueError('too_many_redirects')
    finally:
        if owned:
            client.close()


def enrich_sources(data):
    payload = data.model_dump(mode='json')
    details = {}
    for source in payload['sources']:
        fallback = clean_text(source['title'] + '\n' + source['text'])[:20000]
        try:
            source['text'] = fetch_article(source['url'])
            details[source['source_id']] = {'mode': 'full_article'}
        except (httpx.HTTPError, ValueError, OSError, KeyError):
            source['text'] = fallback
            details[source['source_id']] = {'mode': 'rss_fallback'}
    return payload, details
