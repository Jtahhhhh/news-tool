"""Lazy provider loading keeps adapters independent of each other's modules."""
from importlib import import_module

PROVIDERS = {
    'gemini': ('.gemini', 'GeminiProvider'),
    'deepseek': ('.deepseek', 'DeepSeekProvider'),
}


def provider_class(name):
    from .base import ProviderFailure
    if name not in PROVIDERS:
        raise ProviderFailure('Nhà cung cấp không được bật')
    module, cls = PROVIDERS[name]
    return getattr(import_module(module, package='app.llm'), cls)
