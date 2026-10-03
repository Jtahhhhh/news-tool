"""Single-operator, signed HttpOnly sessions. Configuration fails closed in production."""
import hashlib
import hmac
import os
import secrets
import time
from collections import OrderedDict
from threading import Lock
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix='/api/auth')
COOKIE = 'dashboard_session'
attempts = OrderedDict()
attempt_lock = Lock()


def configured():
    return (all(os.getenv(k) for k in ('DASHBOARD_EMAIL', 'DASHBOARD_PASSWORD_HASH', 'DASHBOARD_SESSION_SECRET'))
            and len(os.getenv('DASHBOARD_SESSION_SECRET', '')) >= 32)


def required():
    from app.config import get_settings
    return get_settings().app_env == 'production' or any(os.getenv(k) for k in ('DASHBOARD_EMAIL', 'DASHBOARD_PASSWORD_HASH', 'DASHBOARD_SESSION_SECRET'))


def signature(value):
    return hmac.new(os.environ['DASHBOARD_SESSION_SECRET'].encode(), value.encode(), hashlib.sha256).hexdigest()


def valid_session(token):
    if not configured(): return False
    try:
        expires, nonce, sig = token.split('.')
        return int(expires) > time.time() and hmac.compare_digest(signature(f'{expires}.{nonce}'), sig)
    except (ValueError, TypeError):
        return False


def password_matches(password, encoded):
    try:
        algorithm, salt, expected = encoded.split('$')
        if algorithm != 'scrypt': return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


async def protect(request: Request, call_next):
    path = request.url.path
    public = path in ('/health', '/api/auth/session', '/api/auth/login') or path.startswith(('/dashboard', '/static/'))
    if required() and not public:
        if not configured(): return JSONResponse({'detail': 'Dashboard authentication is not configured'}, status_code=503)
        if not valid_session(request.cookies.get(COOKIE, '')):
            if path == '/tiktok/callback':
                # A cross-site OAuth navigation does not send Strict cookies.
                # A deliberate same-site navigation can resume the callback;
                # no code is exchanged and no account is changed on this page.
                from html import escape
                from urllib.parse import urlencode
                query = urlencode({k: request.query_params[k] for k in ('state', 'code', 'error') if k in request.query_params})
                target = escape('/tiktok/callback?' + query, quote=True)
                return HTMLResponse('<!doctype html><html lang="vi"><meta name="referrer" content="no-referrer">'
                    '<title>Tiếp tục kết nối TikTok</title><h1>Tiếp tục kết nối TikTok</h1>'
                    '<p>Phiên đăng nhập được bảo vệ bằng cookie Strict. Nếu đã đăng nhập, chọn tiếp tục.</p>'
                    f'<a href="{target}">Tiếp tục trong phiên hiện tại</a>'
                    '<p>Nếu chưa đăng nhập, <a href="/dashboard/" target="_blank" rel="noreferrer">đăng nhập</a> rồi quay lại đây.</p></html>',
                    headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                             'Content-Security-Policy': "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
            return JSONResponse({'detail': 'Đăng nhập để tiếp tục'}, status_code=401)
    response = await call_next(request)
    if path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/session')
def session(request: Request):
    return {'authenticated': not required() or valid_session(request.cookies.get(COOKIE, '')),
            'required': required(), 'csrf_token': request.state.csrf_token}


class Login(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)


@router.post('/login')
def login(payload: Login, request: Request):
    if not configured(): raise HTTPException(503, 'Chưa cấu hình đăng nhập')
    peer = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    with attempt_lock:
        count, start = attempts.get(peer, (0, now))
        if now - start >= 300: count, start = 0, now
        if count >= 10: raise HTTPException(429, 'Thử đăng nhập lại sau 5 phút')
        attempts[peer] = (count + 1, start)
        attempts.move_to_end(peer)
        while len(attempts) > 5000: attempts.popitem(last=False)
    # Perform the expensive password check even when the email differs.
    ok = password_matches(payload.password, os.environ['DASHBOARD_PASSWORD_HASH'])
    if not ok or not hmac.compare_digest(payload.email.encode(), os.environ['DASHBOARD_EMAIL'].encode()):
        raise HTTPException(401, 'Email hoặc mật khẩu không đúng')
    with attempt_lock: attempts.pop(peer, None)
    value = f'{int(time.time()) + 28800}.{secrets.token_hex(16)}'
    response = JSONResponse({'authenticated': True})
    response.set_cookie(COOKIE, f'{value}.{signature(value)}', httponly=True, secure=request.url.scheme == 'https', samesite='strict', max_age=28800)
    return response


@router.post('/logout')
def logout():
    response = JSONResponse({'authenticated': False})
    response.delete_cookie(COOKIE)
    return response
