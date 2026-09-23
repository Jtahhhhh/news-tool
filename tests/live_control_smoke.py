"""Explicit live smoke: at most one configured credential per provider, one send each.

Run manually inside worker. Never imported by automated test discovery.
"""
import json
import time
import uuid
from pathlib import Path
import httpx


def main():
    reports=[]
    with httpx.Client(base_url='http://web:8000',timeout=15) as client:
        dashboard=client.get('/llm?format=json');dashboard.raise_for_status()
        client.headers['x-csrf-token']=client.cookies['csrf_token']
        client.headers['Accept']='application/json'
        metadata=dashboard.json()
        for provider in ('gemini','deepseek'):
            candidates=[c for c in metadata['credentials'] if c['provider']==provider and c['enabled'] and c['state']=='ready']
            if not candidates:
                reports.append(dict(provider=provider,live_call=False,reason='Không có credential ready; không gửi request'))
                continue
            credential=min(candidates,key=lambda c:(c['priority'],c['id']))
            model=credential['allowed_models'][0]
            response=client.post(f'/llm/credentials/{credential["id"]}/test',json=dict(model=model,acknowledge_cost=True,idempotency_key='live-control-'+uuid.uuid4().hex))
            response.raise_for_status();job_id=response.json()['id']
            deadline=time.monotonic()+110
            while time.monotonic()<deadline:
                response=client.get(f'/llm/jobs/{job_id}?format=json');response.raise_for_status();result=response.json()
                if result['job']['status'] in ('succeeded','failed','unknown_outcome','waiting_quota'):break
                time.sleep(1)
            reports.append(dict(provider=provider,credential_id=credential['id'],model=model,live_call=result['job']['attempts']>0,**result))
    path=Path('/tmp/llm-control-live-smoke.json')
    path.write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([dict(provider=r['provider'],live_call=r['live_call'],job_id=r.get('job',{}).get('id'),
                           status=r.get('job',{}).get('status'),http_statuses=[a['http_status'] for a in r.get('attempts',[])]) for r in reports]))


if __name__=='__main__':main()
