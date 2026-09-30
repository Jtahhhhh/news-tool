import os
from pathlib import Path
from urllib.parse import urlsplit
from cryptography.fernet import Fernet, InvalidToken


def secret(name):
    filename = os.getenv(name + '_FILE', '')
    return Path(filename).read_text(encoding='utf-8').strip() if filename else os.getenv(name, '').strip()


def enabled():
    return os.getenv('TIKTOK_ENABLED', 'false').lower() == 'true'


def cipher():
    try:
        return Fernet(secret('TIKTOK_TOKEN_KEY').encode())
    except (ValueError, OSError):
        raise RuntimeError('TikTok encryption key unavailable') from None


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    try:
        return cipher().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        raise RuntimeError('TikTok encrypted data unavailable; reconnect account') from None


def credentials():
    return dict(client_key=secret('TIKTOK_CLIENT_KEY'), client_secret=secret('TIKTOK_CLIENT_SECRET'))


def redirect_uri():
    return os.getenv('TIKTOK_REDIRECT_URI', '')


def require_config():
    if not enabled():
        raise RuntimeError('TikTok chưa bật trong cấu hình')
    if os.getenv('TIKTOK_DIRECT_POST_ENABLED', 'false').lower() == 'true':
        raise RuntimeError('Direct Post chưa được triển khai; giữ cờ này false')
    uri = urlsplit(redirect_uri())
    if (uri.scheme != 'https' or not uri.hostname or uri.query or uri.fragment
            or uri.username or uri.password or uri.path != '/tiktok/callback'):
        raise RuntimeError('Cần HTTPS redirect URI chính xác, kết thúc /tiktok/callback')
    if not all(credentials().values()):
        raise RuntimeError('Thiếu TikTok client key/secret')
    cipher()


def timeout():
    return max(5, min(120, float(os.getenv('TIKTOK_TIMEOUT_SECONDS', '60'))))
