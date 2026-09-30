from contextlib import contextmanager
from datetime import timedelta
import hashlib
import secrets
from urllib.parse import urlencode
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.database import engine, session_scope
from app.models import TikTokAccount, TikTokOAuthState, utcnow
from app.tiktok import config
from app.tiktok.api import APIError


def digest(value): return hashlib.sha256(value.encode()).hexdigest()


@contextmanager
def account_lock(account_id):
    # Session advisory lock survives commits around token rotation. Separate from
    # publish-job locks; disconnect and refresh obey the same lock order.
    with engine.connect() as connection:
        connection.execute(text('SELECT pg_advisory_lock(721007, :id)'), {'id':account_id})
        connection.commit()
        try:
            with Session(bind=connection, expire_on_commit=False) as session:
                yield session
        finally:
            connection.rollback()
            connection.execute(text('SELECT pg_advisory_unlock(721007, :id)'), {'id':account_id})
            connection.commit()


def begin_oauth():
    config.require_config()
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with session_scope() as s:
        s.add(TikTokOAuthState(state_hash=digest(state), browser_hash=digest(browser),
                              expires_at=utcnow()+timedelta(minutes=10)))
    url = 'https://www.tiktok.com/v2/auth/authorize/?' + urlencode(dict(
        client_key=config.credentials()['client_key'], response_type='code',
        scope='user.info.basic,video.upload', redirect_uri=config.redirect_uri(), state=state,
        disable_auto_auth=1))
    return url, browser


def consume_state(state, browser):
    with session_scope() as s:
        row = s.scalar(select(TikTokOAuthState).where(TikTokOAuthState.state_hash==digest(state)).with_for_update())
        if (not row or not browser or row.used_at or row.expires_at <= utcnow()
                or not secrets.compare_digest(row.browser_hash, digest(browser))):
            raise HTTPException(400, 'Phiên kết nối không hợp lệ, hết hạn hoặc đã sử dụng')
        row.used_at = utcnow()


def apply_tokens(account, payload):
    try:
        if payload['open_id'] != account.open_id or payload.get('token_type','Bearer').lower() != 'bearer':
            raise ValueError()
        scope = payload['scope'].split(',')
        if 'video.upload' not in scope: raise ValueError()
        access, refresh = payload['access_token'], payload['refresh_token']
        if not isinstance(access,str) or not access or not isinstance(refresh,str) or not refresh: raise ValueError()
        exp, rexp = int(payload['expires_in']), int(payload['refresh_expires_in'])
        if not 0 < exp <= 31_536_000 or not 0 < rexp <= 63_072_000: raise ValueError()
        account.access_cipher, account.refresh_cipher = config.encrypt(access), config.encrypt(refresh)
        account.access_expires, account.refresh_expires = utcnow()+timedelta(seconds=exp), utcnow()+timedelta(seconds=rexp)
        account.scopes, account.state = scope, 'connected'
    except (KeyError, TypeError, ValueError):
        raise APIError('invalid_response', uncertain=True) from None


def save_connection(payload, profile):
    open_id = payload.get('open_id')
    if not isinstance(open_id,str) or not 1 <= len(open_id) <= 200 or profile.get('open_id') != open_id:
        raise APIError('invalid_response', uncertain=True)
    # Serialize first insert as well as reconnects.
    with session_scope() as s:
        s.execute(text('SELECT pg_advisory_xact_lock(721008)'))
        row = s.scalar(select(TikTokAccount).where(TikTokAccount.open_id==open_id))
        if not row:
            row = TikTokAccount(open_id=open_id,display_name=str(profile.get('display_name') or open_id[-8:])[:200])
            apply_tokens(row,payload)
            s.add(row);s.flush()
            return row.id
        account_id = row.id
    with account_lock(account_id) as s:
        row = s.get(TikTokAccount, account_id)
        apply_tokens(row,payload)
        row.display_name = str(profile.get('display_name') or open_id[-8:])[:200]
        s.commit()
    return account_id


def access_token(account_id, api):
    with account_lock(account_id) as s:
        row = s.get(TikTokAccount,account_id)
        if not row or row.state != 'connected' or 'video.upload' not in row.scopes:
            raise APIError('access_token_invalid')
        if row.access_expires > utcnow()+timedelta(seconds=120):
            return config.decrypt(row.access_cipher)
        if row.refresh_expires <= utcnow():
            row.state='reconnect_required';s.commit()
            raise APIError('access_token_invalid')
        refresh = config.decrypt(row.refresh_cipher)
        # Crash/timeout after a possibly rotating refresh requires reconnect,
        # never replay the refresh automatically with a potentially stale token.
        row.state='refreshing';s.commit()
        try:
            apply_tokens(row,api.refresh(refresh))
        except Exception:
            row.state='reconnect_required';s.commit()
            raise APIError('access_token_invalid') from None
        s.commit()
        return config.decrypt(row.access_cipher)


def disconnect(account_id, api):
    with account_lock(account_id) as s:
        row = s.get(TikTokAccount,account_id)
        if not row: raise HTTPException(404, 'Không tìm thấy tài khoản')
        if row.state=='disconnected': return
        # Stop further sending before attempting remote revoke.
        row.state='disconnecting';s.commit()
        try:
            api.revoke(config.decrypt(row.access_cipher))
        except Exception:
            row.state='revoke_unconfirmed';s.commit()
            raise HTTPException(502, 'Đã chặn gửi local; chưa xác nhận thu hồi quyền. Thử ngắt lại hoặc thu hồi trong TikTok.') from None
        row.state='disconnected';row.access_cipher='';row.refresh_cipher='';s.commit()
