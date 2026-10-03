"""One persistent administrator, with revocable eight-hour sessions."""
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict
from datetime import timedelta
from threading import Lock
from fastapi import APIRouter, Request, HTTPException, Form, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, text
from app.config import get_settings
from app.database import session_scope
from app.models import AdminUser, AdminSession, utcnow

router = APIRouter()
COOKIE = 'dashboard_session'
attempts = OrderedDict()
attempt_lock = Lock()


def secure_cookie():
    settings = get_settings()
    return settings.app_env == 'production' or settings.session_secure


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
    return f'scrypt${salt.hex()}${digest}'


def password_matches(password, encoded):
    try:
        algorithm, salt, expected = encoded.split('$')
        if algorithm != 'scrypt': return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def bootstrap_admin():
    settings = get_settings()
    if len(settings.auth_secret_key) < 32:
        raise RuntimeError('AUTH_SECRET_KEY must contain at least 32 characters')
    with session_scope() as db:
        # Serialize bootstrap across web replicas; the singleton constraint also guards it.
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(721002)'))
        if db.get(AdminUser, 1) is None:
            if not settings.admin_username or len(settings.admin_username) > 254 or not settings.admin_password:
                raise RuntimeError('Initial startup requires ADMIN_USERNAME and ADMIN_PASSWORD')
            db.add(AdminUser(id=1, username=settings.admin_username,
                             password_hash=password_hash(settings.admin_password)))


def token_hash(token):
    return hmac.new(get_settings().auth_secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def valid_session(token):
    if not token or len(token) > 128 or len(get_settings().auth_secret_key) < 32:
        return False
    with session_scope() as db:
        session = db.get(AdminSession, token_hash(token))
        return bool(session and session.expires_at.replace(tzinfo=utcnow().tzinfo) > utcnow())


def require_admin(request: Request):
    if not getattr(request.state, 'admin_authenticated', False):
        if not valid_session(request.cookies.get(COOKIE, '')):
            raise HTTPException(401, 'Đăng nhập để tiếp tục')
        request.state.admin_authenticated = True
    return 1


def is_api(path):
    return path.startswith(('/api/', '/script-jobs', '/video-jobs', '/publish-jobs', '/assets'))


async def protect(request: Request, call_next):
    path = request.url.path
    public = path in ('/health', '/login', '/api/auth/session', '/api/auth/login') or path.startswith(('/static/', '/dashboard/assets/'))
    if not public:
        try:
            require_admin(request)
        except HTTPException:
            if is_api(path) or request.method != 'GET':
                return JSONResponse({'detail': 'Đăng nhập để tiếp tục'}, status_code=401)
            return RedirectResponse('/login', status_code=303)
    response = await call_next(request)
    if not path.startswith(('/static/', '/dashboard/assets/')):
        response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/login')
def login_page(request: Request):
    from app.main import templates
    if valid_session(request.cookies.get(COOKIE, '')):
        return RedirectResponse('/', status_code=303)
    return templates.TemplateResponse(request=request, name='login.html', context={})


@router.get('/api/auth/session')
def session(request: Request):
    return {'authenticated': valid_session(request.cookies.get(COOKIE, '')),
            'required': True, 'csrf_token': request.state.csrf_token}


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024, repr=False)


def authenticate(username, password, request, response):
    peer = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    with attempt_lock:
        count, start = attempts.get(peer, (0, now))
        if now - start >= 300: count, start = 0, now
        if count >= 10: raise HTTPException(429, 'Thử đăng nhập lại sau 5 phút')
        attempts[peer] = (count + 1, start)
        attempts.move_to_end(peer)
        while len(attempts) > 5000: attempts.popitem(last=False)
    token = secrets.token_urlsafe(32)
    with session_scope() as db:
        admin = db.get(AdminUser, 1)
        if admin is None: raise HTTPException(503, 'Admin chưa được cấu hình')
        ok = password_matches(password, admin.password_hash)
        if not ok or not hmac.compare_digest(username.encode(), admin.username.encode()):
            raise HTTPException(401, 'Username hoặc mật khẩu không đúng')
        admin.last_login_at = utcnow()
        old = request.cookies.get(COOKIE, '')
        db.execute(delete(AdminSession).where((AdminSession.expires_at <= utcnow()) | (AdminSession.token_hash == token_hash(old))))
        db.add(AdminSession(token_hash=token_hash(token), admin_id=1, expires_at=utcnow() + timedelta(hours=8)))
    with attempt_lock: attempts.pop(peer, None)
    response.set_cookie(COOKIE, token, httponly=True, secure=secure_cookie(), samesite='lax', max_age=28800)
    return response


@router.post('/login')
def form_login(request: Request, username: str = Form(max_length=254), password: str = Form(max_length=1024)):
    from app.main import templates
    try:
        return authenticate(username, password, request, RedirectResponse('/', status_code=303))
    except HTTPException as exc:
        return templates.TemplateResponse(request=request, name='login.html', context={'error': exc.detail}, status_code=exc.status_code)


@router.post('/api/auth/login')
def login(payload: Login, request: Request):
    return authenticate(payload.username, payload.password, request, JSONResponse({'authenticated': True}))


@router.post('/logout', dependencies=[Depends(require_admin)])
@router.post('/api/auth/logout', dependencies=[Depends(require_admin)])
def logout(request: Request):
    with session_scope() as db:
        db.execute(delete(AdminSession).where(AdminSession.token_hash == token_hash(request.cookies.get(COOKIE, ''))))
    response = JSONResponse({'authenticated': False}) if request.url.path.startswith('/api/') else RedirectResponse('/login', status_code=303)
    response.delete_cookie(COOKIE, httponly=True, secure=secure_cookie(), samesite='lax')
    return response
