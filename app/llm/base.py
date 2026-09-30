import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx


@dataclass
class Generation:
    raw: str
    usage: dict = field(default_factory=dict)
    elapsed: float = 0
    request_id: str | None = None
    provider: str = ''
    model: str = ''
    diagnostics: dict = field(default_factory=dict)


class ProviderFailure(Exception):
    def __init__(self, message, *, status=None, retry_after=None, request_id=None,
                 uncertain=False, raw='', usage=None, elapsed=0, kind=None, diagnostics=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.request_id = request_id
        self.uncertain = uncertain
        self.raw = raw
        self.usage = usage or {}
        self.elapsed = elapsed
        self.diagnostics = diagnostics or {}
        self.kind = kind or ('unknown_outcome' if uncertain else 'service_error' if status in (500,502,503,504)
                            else 'rate_limit' if status == 429 else 'configuration_error' if status in (400,401,403,404,422)
                            else 'validation_error' if raw else 'configuration_error')

    @property
    def retryable(self):
        return not self.uncertain and self.kind in ('rate_limit', 'service_error')


def retry_after_seconds(value):
    if value is None:
        return None
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError, AttributeError):
            return None


def redact(value):
    from .secrets import redact_secrets
    return redact_secrets(value)


def payload_hash(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ResponseContentError(ValueError):
    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind


DIAGNOSTIC_HEADERS = {'retry-after', 'date', 'x-request-id', 'x-goog-request-id',
                      'x-ratelimit-limit-requests', 'x-ratelimit-remaining-requests',
                      'x-ratelimit-reset-requests', 'server-timing'}


class LLMProvider(ABC):
    name = 'unknown'
    def __init__(self, model, key='', timeout=60, output_limit=8192, client=None):
        self.model, self.key = model, key
        self.timeout, self.output_limit = timeout, output_limit
        self.client = client

    @abstractmethod
    def build_request(self, data, prompt, feedback):
        pass

    @abstractmethod
    def parse_response(self, body):
        pass

    def generate_script(self, data, prompt, feedback=''):
        if not self.key:
            raise ProviderFailure('Chưa cấu hình API key cho nhà cung cấp này')
        from .secrets import _resolved
        _resolved.add(self.key)
        url, headers, payload = self.build_request(data, prompt, feedback)
        frozen = getattr(self, 'prepared_request', None)
        if frozen:
            url, payload = frozen['endpoint'], frozen['payload']
        return self.send_http(url, headers, payload)

    def send_http(self, url, headers, payload):
        """Exactly one send. Durable retry is exclusively the job manager's job."""
        started = time.monotonic()
        provider_name = self.name
        diagnostic = dict(endpoint=url, payload_hash=payload_hash(payload), provider=provider_name, model=self.model)
        owned = self.client is None
        client = self.client or httpx.Client(timeout=self.timeout, follow_redirects=False, transport=httpx.HTTPTransport(retries=0))
        try:
            response = client.post(url, headers=headers, json=payload, timeout=self.timeout)
            elapsed = time.monotonic() - started
            request_id = response.headers.get('x-request-id') or response.headers.get('x-goog-request-id')
            request_id = redact(request_id) if request_id else None
            diagnostic.update(http_status=response.status_code,
                headers={k:redact(v)[:1000] for k,v in response.headers.items() if k.lower() in DIAGNOSTIC_HEADERS},
                retry_after=response.headers.get('retry-after'))
            if response.status_code >= 400:
                from .failures import classify
                try: error_body = response.json()
                except ValueError: error_body = {}
                error_data = error_body.get('error', {}) if isinstance(error_body, dict) else {}
                diagnostic['provider_code'] = redact(str(error_data.get('status', error_data.get('code', '')))) if isinstance(error_data, dict) else ''
                if isinstance(error_data,dict):
                    diagnostic['field_violations'] = [v for d in error_data.get('details',[]) if isinstance(d,dict)
                        for v in d.get('fieldViolations',[]) if isinstance(v,dict)] if isinstance(error_data.get('details',[]),list) else []
                diagnostic['quota_classification'] = ('confirmed_daily' if classify(provider_name,response.status_code,error_body)=='daily_quota'
                    else 'rate_or_unknown_quota') if response.status_code==429 else None
                raise ProviderFailure(
                    f'HTTP {response.status_code}: ' + redact(response.text[:4000]),
                    status=response.status_code, request_id=request_id,
                    retry_after=retry_after_seconds(response.headers.get('retry-after')), elapsed=elapsed,
                    kind=classify(provider_name,response.status_code,error_body), diagnostics=diagnostic,
                    usage=json.loads(redact(json.dumps(error_body.get('usageMetadata',error_body.get('usage',{})) if isinstance(error_body,dict) else {}))))
            try:
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object')
                raw, usage = self.parse_response(body)
            except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
                raise ProviderFailure('Phản hồi không hoàn chỉnh: ' + str(exc),
                                      raw=redact(response.text), request_id=request_id, elapsed=elapsed,
                                      status=response.status_code, kind=getattr(exc, 'kind', 'response_parse_error'), diagnostics=diagnostic,
                                      usage=json.loads(redact(json.dumps(body.get('usageMetadata', body.get('usage', {}))
                                             if isinstance(locals().get('body'), dict) else {})))) from None
            return Generation(redact(raw), json.loads(redact(json.dumps(usage))), elapsed, request_id, provider_name, self.model, diagnostic)
        except httpx.TransportError:
            raise ProviderFailure('Kết quả chưa xác định do mạng/timeout; không tự gửi lại vì có thể đã tính phí',
                                  uncertain=True, elapsed=time.monotonic() - started, diagnostics=diagnostic) from None
        finally:
            if owned:
                client.close()


def user_content(data, feedback):
    from .prompts.news_script import build_news_script_prompt
    return build_news_script_prompt(data, feedback)


class FakeProvider(LLMProvider):
    def build_request(self, data, prompt, feedback):
        raise NotImplementedError

    def parse_response(self, body):
        raise NotImplementedError

    def generate_script(self, data, prompt, feedback=''):
        source = data.sources[0]
        quote = source.text[:300]
        result = dict(schema_version='1.0', story_id=data.story_id, decision='draft', reason='',
                      title=source.title, hook=quote, caption=quote,
                      claims=[dict(claim_id='c1', text=quote, evidence=[dict(source_id=source.source_id, quote=quote)])],
                      scenes=[dict(scene_id=1, seconds=data.target_seconds, narration=quote,
                                   on_screen_text='', visual_brief='Hình minh họa', claim_ids=['c1'])],
                      warnings=['DỮ LIỆU GIẢ LẬP: chỉ để kiểm thử; không dùng để dựng video.'])
        return Generation(json.dumps(result, ensure_ascii=False))


def get_provider(name, model, timeout, output_limit):
    from app.config import get_settings
    from .registry import provider_class
    settings = get_settings()
    if name == 'fake' and settings.llm_allow_fake:
        return FakeProvider(model)
    cls = provider_class(name)
    return cls(model, getattr(settings, name + '_api_key', ''), timeout, output_limit)
