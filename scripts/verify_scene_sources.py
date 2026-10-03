"""Compare public article with the saved snapshot; no LLM calls or DB writes."""
import json
from pathlib import Path
from app.services.article_text import fetch_article
from app.llm.provider_schema import groq_schema, GROQ_SCHEMA_PROFILE
from app.llm.schemas import Output

root=Path('/tmp/scene-audit')
data=json.loads((root/'input.json').read_text())
checks=[]
for source in data['sources']:
    try:
        current=fetch_article(source['url'])
        checks.append(dict(source_id=source['source_id'],url=source['url'],
            snapshot_characters=len(source['text']),current_characters=len(current),
            exact_match=current==source['text'],current_text=current))
    except Exception as exc:
        checks.append(dict(source_id=source['source_id'],error=type(exc).__name__,verified=False))
(root/'source-comparison.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
(root/'provider-schema.json').write_text(json.dumps(dict(profile=GROQ_SCHEMA_PROFILE,schema=groq_schema(Output.model_json_schema())),ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{k:v for k,v in c.items() if k!='current_text'} for c in checks]))
