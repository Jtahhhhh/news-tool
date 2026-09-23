"""Add synthetic control-plane fixtures without changing real newsroom data."""
from app.database import engine,session_scope
from app.models import LLMCredential,LLMPolicy
from app.services.llm_control import get_policy
from sqlalchemy import select

if not engine.url.database.endswith('_ui_test'):
    raise RuntimeError('Requires *_ui_test')
with session_scope() as session:
    p=get_policy(session);p['fallback_enabled']=True
    session.get(LLMPolicy,1).data=p
    for provider in ('gemini','deepseek'):
        if not session.scalar(select(LLMCredential.id).where(LLMCredential.name==f'MOCK {provider}')):
            session.add(LLMCredential(name=f'MOCK {provider}',provider=provider,project_id=f'mock-{provider}',
              quota_group=f'mock-{provider}',secret_ref=f'env:LLM_KEY_UI_{provider.upper()}',
              allowed_models=[r['model'] for r in p['routes'] if r['provider']==provider],priority=0))
print('Isolated control UI fixture ready')
