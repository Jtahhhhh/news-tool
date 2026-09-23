"""Separate explicit live evaluation; no scheduled calls and no automatic retries."""
import argparse
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from app.config import get_settings
from app.llm.base import ProviderFailure, get_provider, redact
from app.llm.schemas import Input, Output, validate_output


def run(provider_name, fixture, destination):
    settings = get_settings()
    model = getattr(settings, f'{provider_name}_model')
    prompt = Path(__file__).with_name('prompts').joinpath('script_v1.txt').read_text(encoding='utf-8')
    report = dict(run_id=str(uuid.uuid4()), created_at=datetime.now(timezone.utc).isoformat(),
                  fixture=fixture.name, provider=provider_name, model=model, prompt_version='1.0', schema_version='1.0',
                  live_call=False, attempts=0, retries=0, structural_pass=False, human_review_required=True,
                  semantic_factuality_checked=False, usage=None,
                  prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                  schema_sha256=hashlib.sha256(json.dumps(Output.model_json_schema(),sort_keys=True).encode()).hexdigest())
    started=time.monotonic()
    try:
        data=Input.model_validate_json(fixture.read_text(encoding='utf-8'))
        report['input']=data.model_dump(mode='json')
        adapter=get_provider(provider_name,model,settings.llm_timeout_seconds,settings.llm_max_output_tokens)
        if not adapter.key:raise ProviderFailure('Chưa cấu hình API key; không gửi request')
        report.update(live_call=True, attempts=1)
        response=adapter.generate_script(data,prompt)
        report.update(raw_output=response.raw,usage=response.usage,request_id=response.request_id)
        output=validate_output(data,response.raw)
        report.update(structural_pass=True,output=output.model_dump(mode='json'),decision=output.decision)
    except ProviderFailure as exc:
        report.update(error=redact(str(exc)),http_status=exc.status,request_id=exc.request_id,
                      unknown_outcome=exc.uncertain,raw_output=exc.raw,usage=exc.usage,
                      retry_after_seconds=exc.retry_after)
    except ValueError as exc:
        report.update(error=redact(str(exc)),error_kind='validation_error')
    report['elapsed_seconds']=round(time.monotonic()-started,3)
    destination.mkdir(parents=True,exist_ok=True)
    path=destination / (report['run_id']+'.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report.get(k) for k in ('run_id','provider','fixture','structural_pass','live_call','http_status','elapsed_seconds')},ensure_ascii=False),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--provider',choices=['gemini','deepseek'],required=True)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--fixtures',type=Path)
    parser.add_argument('--repeat',type=int,default=1)
    parser.add_argument('--out',type=Path,default=Path('results/llm-integration'))
    args=parser.parse_args()
    if not 1<=args.repeat<=3:parser.error('repeat must be 1..3')
    if bool(args.input)==bool(args.fixtures):parser.error('Choose input OR fixtures')
    fixtures=[args.input] if args.input else [args.fixtures/(name+'.json') for name in ('normal','insufficient','conflict','missing_date','injection')]
    passed=True
    for fixture in fixtures:
        for _ in range(args.repeat):
            report=run(args.provider,fixture,args.out)
            passed=passed and report['structural_pass']
            # A service/account failure is a failed smoke gate, not 15 wasteful requests.
            if report.get('http_status') or report.get('unknown_outcome') or not report['live_call']:
                raise SystemExit(1)
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':main()
