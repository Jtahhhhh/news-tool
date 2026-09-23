import hashlib
import ipaddress
import re
import socket
import unicodedata
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from bs4 import BeautifulSoup


def clean_text(value: str) -> str:
    soup = BeautifulSoup(value or '', 'html.parser')
    for tag in soup(['script', 'style']):
        tag.decompose()
    return ' '.join(unicodedata.normalize('NFKC', soup.get_text(' ', strip=True)).split())


def canonical_url(value: str, base: str = '') -> str:
    parts = urlsplit(urljoin(base, value.strip()))
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('URL phải là HTTP(S), không chứa thông tin đăng nhập')
    host = parts.hostname.lower().encode('idna').decode('ascii')
    if ':' in host:
        host = f'[{host}]'
    port = parts.port
    if port and (parts.scheme.lower(), port) not in [('http', 80), ('https', 443)]:
        host += f':{port}'
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    result = urlunsplit((parts.scheme.lower(), host, parts.path or '/', urlencode(sorted(query)), ''))
    if len(result.encode('utf-8')) > 2000:
        raise ValueError('URL vượt giới hạn 2000 byte')
    return result


def validate_public_url(url: str, allow_private: bool = False):
    url = canonical_url(url)
    if not allow_private:
        host = urlsplit(url).hostname
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError('Nguồn phải dùng địa chỉ Internet công khai')
    return url


def fingerprint(title: str, published_at: datetime | None) -> str:
    normalized = re.sub(r'\W+', ' ', title.casefold()).strip()
    date = published_at.date().isoformat() if published_at else ''
    return hashlib.sha256(f'{normalized}|{date}'.encode()).hexdigest()


def normalize_date(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    # Invalid future dates must not dominate ranking.
    if value.year < 1990 or value.timestamp() > datetime.now(timezone.utc).timestamp() + 86400:
        return None
    return value
