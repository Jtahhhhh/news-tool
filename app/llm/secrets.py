"""Resolve allowlisted references inside the worker; never accept secret values over HTTP."""
import os
import re
from pathlib import Path

_resolved = set()
ENV_NAME = re.compile(r'(?:GEMINI_API_KEY|DEEPSEEK_API_KEY|LLM_KEY_[A-Z0-9_]{1,100})\Z')
FILE_NAME = re.compile(r'llm_[a-zA-Z0-9_-]{1,100}\Z')


def valid_reference(reference):
    kind, _, name = reference.partition(':')
    return bool((kind == 'env' and ENV_NAME.fullmatch(name)) or (kind == 'secret' and FILE_NAME.fullmatch(name)))


def resolve(reference):
    if not valid_reference(reference):
        return ''
    kind, name = reference.split(':', 1)
    if kind == 'env':
        value = os.getenv(name, '')
        if not value and name in ('GEMINI_API_KEY', 'DEEPSEEK_API_KEY'):
            from app.config import get_settings
            value = getattr(get_settings(), name.lower())
    else:
        path = Path('/run/secrets') / name
        try:
            if path.is_symlink() or path.stat().st_size > 16384:
                return ''
            value = path.read_text(encoding='utf-8')
        except OSError:
            return ''
    value = value.strip()
    if value:
        _resolved.add(value)
    return value


def mask(value):
    return '••••' + value[-4:] if len(value) >= 12 else '••••'


def redact_secrets(value):
    from app.config import get_settings
    settings = get_settings()
    candidates = _resolved | {v for k, v in os.environ.items() if ENV_NAME.fullmatch(k)} | {settings.gemini_api_key, settings.deepseek_api_key}
    text = str(value)
    for secret in sorted((x for x in candidates if x), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)(bearer\s+)[^\s"\x27,}]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:authorization|x-goog-api-key|api[_-]?key)["\x27]?\s*[:=]\s*["\x27]?)[^\s"\x27,}]+', r'\1[REDACTED]', text)
    return text
