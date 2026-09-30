"""Bounded adapter: never include raw responses or secrets in exceptions."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
import httpx
from app.tiktok.config import credentials, redirect_uri, timeout

BASE = 'https://open.tiktokapis.com'
SAFE_CODES = {'access_token_invalid', 'scope_not_authorized', 'rate_limit_exceeded',
              'invalid_grant', 'invalid_client', 'invalid_request', 'invalid_param',
              'invalid_publish_id', 'token_not_authorized_for_specified_publish_id',
              'spam_risk_too_many_pending_share', 'spam_risk_user_banned_from_posting', 'internal_error'}


class APIError(Exception):
    def __init__(self, code='provider_error', status=None, uncertain=False, retry_after=0):
        self.code = code if isinstance(code,str) and code in SAFE_CODES | {'network_error', 'invalid_response', 'transfer_error', 'unsafe_upload_url'} else 'provider_error'
        self.status, self.uncertain, self.retry_after = status, uncertain, retry_after
        super().__init__(self.code)


def retry_seconds(value):
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        try:
            return max(0, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            return 0


def chunks(size):
    if not 0 < size <= 4_000_000_000:
        raise ValueError('Video phải lớn hơn 0 và không quá 4 GB')
    chunk = min(size, 32_000_000)
    count = max(1, size // chunk)
    return chunk, count


def upload_url(value):
    try:
        u = urlsplit(value)
        host = u.hostname or ''
        valid_host = host == 'open-upload.tiktokapis.com' or (host.startswith('upload.') and host.endswith('.tiktokapis.com'))
        if u.scheme != 'https' or not valid_host or u.port not in (None,443) or u.username or u.password or u.fragment:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise APIError('unsafe_upload_url', uncertain=True) from None


class TikTokAPI:
    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=timeout(), follow_redirects=False, trust_env=False)
        self.owns_client = client is None

    def __enter__(self): return self
    def __exit__(self, *_):
        if self.owns_client: self.client.close()

    def request(self, path, token=None, form=None, body=None, method='POST'):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        try:
            r = self.client.request(method, BASE + path, headers=headers, data=form, json=body)
        except httpx.HTTPError:
            raise APIError('network_error', uncertain=True) from None
        try:
            payload = r.json()
            if not isinstance(payload, dict): raise ValueError()
        except ValueError:
            raise APIError('invalid_response', r.status_code, uncertain=True) from None
        error = payload.get('error')
        code = error.get('code') if isinstance(error, dict) else error
        if r.status_code >= 300 or (code and code != 'ok'):
            raise APIError(code, r.status_code, uncertain=r.status_code >= 500 or 300 <= r.status_code < 400,
                           retry_after=retry_seconds(r.headers.get('retry-after')))
        if form is not None: return payload
        if code != 'ok' or not isinstance(payload.get('data'), dict):
            raise APIError('invalid_response', r.status_code, uncertain=True)
        return payload['data']

    def exchange(self, code):
        return self.request('/v2/oauth/token/', form={**credentials(), 'grant_type':'authorization_code',
                            'code':code, 'redirect_uri':redirect_uri()})

    def refresh(self, refresh_token):
        return self.request('/v2/oauth/token/', form={**credentials(), 'grant_type':'refresh_token', 'refresh_token':refresh_token})

    def revoke(self, access_token):
        return self.request('/v2/oauth/revoke/', form={**credentials(), 'token':access_token})

    def profile(self, token):
        return self.request('/v2/user/info/?fields=open_id,display_name', token, method='GET')['user']

    def initialize(self, token, size):
        chunk, count = chunks(size)
        result = self.request('/v2/post/publish/inbox/video/init/', token, body={'source_info':{
            'source':'FILE_UPLOAD', 'video_size':size, 'chunk_size':chunk, 'total_chunk_count':count}})
        if not isinstance(result.get('publish_id'), str) or not 1 <= len(result['publish_id']) <= 64:
            raise APIError('invalid_response', uncertain=True)
        return result

    def status(self, token, publish_id):
        return self.request('/v2/post/publish/status/fetch/', token, body={'publish_id':publish_id})

    def transfer(self, url, data, offset, size):
        url = upload_url(url)
        try:
            response = self.client.put(url, content=data, headers={'Content-Type':'video/mp4',
                'Content-Length':str(len(data)), 'Content-Range':f'bytes {offset}-{offset+len(data)-1}/{size}'})
        except httpx.HTTPError:
            raise APIError('network_error', uncertain=True) from None
        expected = 201 if offset + len(data) == size else 206
        if response.status_code != expected:
            raise APIError('transfer_error', response.status_code, uncertain=True,
                           retry_after=retry_seconds(response.headers.get('retry-after')))
