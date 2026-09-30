from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from app.database import session_scope
from app.models import TikTokAccount, VideoVersion, PublishJob, PublishAttempt
from app.services.publishing import CreatePublish, enqueue, eligible, public_job, cancel, reconcile, LABELS
from app.tiktok import config
from app.tiktok.accounts import begin_oauth, consume_state, save_connection, disconnect
from app.tiktok.api import TikTokAPI

router=APIRouter()


def require_config():
    try:config.require_config()
    except (RuntimeError,OSError,ValueError):
        raise HTTPException(503,'TikTok chưa được cấu hình đầy đủ; xem docs/HUONG-DAN-TIKTOK.md') from None


@router.get('/publishing')
def dashboard(request:Request):
    from app.main import templates
    with session_scope() as s:
        accounts=s.scalars(select(TikTokAccount).order_by(TikTokAccount.id)).all()
        versions=s.scalars(select(VideoVersion).where(VideoVersion.status=='approved').order_by(VideoVersion.id.desc()).limit(100)).all()
        candidates=[];unavailable=[]
        for version in versions:
            try:
                eligible(s,version.id)
                candidates.append(version)
            except (HTTPException,ValueError) as exc:
                unavailable.append({'id':version.id,'reason':exc.detail if isinstance(exc,HTTPException) else 'Dung lượng không hợp lệ'})
        jobs=s.scalars(select(PublishJob).order_by(PublishJob.id.desc()).limit(100)).all()
        ready=True
        try:config.require_config()
        except (RuntimeError,OSError,ValueError):ready=False
        return templates.TemplateResponse(request=request,name='publishing.html',context=dict(
            accounts=accounts,versions=candidates,unavailable=unavailable,jobs=jobs,labels=LABELS,ready=ready))


@router.post('/tiktok/connect')
def connect(request:Request):
    require_config()
    # OAuth binding cookie must be set on the same HTTPS host as the callback.
    if urlsplit(str(request.url)).netloc!=urlsplit(config.redirect_uri()).netloc or request.url.scheme!='https':
        raise HTTPException(409,'Mở ứng dụng bằng HTTPS trên đúng host đã đăng ký callback trước khi kết nối')
    url,browser=begin_oauth()
    response=RedirectResponse(url,status_code=303)
    response.set_cookie('tiktok_oauth_browser',browser,max_age=600,httponly=True,secure=True,samesite='lax',path='/tiktok')
    return response


@router.get('/tiktok/callback')
def callback(request:Request,state:str='',code:str='',error:str=''):
    require_config()
    consume_state(state,request.cookies.get('tiktok_oauth_browser',''))
    if error or not code:raise HTTPException(400,'TikTok chưa cấp quyền; hãy kết nối lại')
    try:
        with TikTokAPI() as api:
            payload=api.exchange(code)
            profile=api.profile(payload['access_token'])
            save_connection(payload,profile)
    except Exception:
        raise HTTPException(502,'Không hoàn tất kết nối TikTok; kiểm tra quyền app và kết nối lại') from None
    response=RedirectResponse('/publishing',status_code=303)
    response.delete_cookie('tiktok_oauth_browser',path='/tiktok',secure=True,samesite='lax')
    return response


@router.post('/tiktok/accounts/{account_id}/disconnect')
def disconnect_account(account_id:int):
    require_config()
    with TikTokAPI() as api:disconnect(account_id,api)
    return RedirectResponse('/publishing',status_code=303)


@router.post('/publish-jobs',status_code=202)
def create(payload:CreatePublish):
    require_config()
    with session_scope() as s:return public_job(enqueue(s,payload))


@router.get('/publish-jobs/{job_id}')
def job(job_id:int):
    with session_scope() as s:
        row=s.get(PublishJob,job_id)
        if not row:raise HTTPException(404,'Không tìm thấy job')
        result=public_job(row)
        result['attempts']=[dict(operation=a.operation,outcome=a.outcome,http_status=a.http_status,
                                created_at=a.created_at.isoformat()) for a in s.scalars(
            select(PublishAttempt).where(PublishAttempt.job_id==job_id).order_by(PublishAttempt.id.desc()).limit(100))]
        return result


@router.post('/publish-jobs/{job_id}/{action}')
def action(job_id:int,action:str):
    if action not in ('cancel','reconcile'):raise HTTPException(404,'Thao tác không tồn tại')
    with session_scope() as s:
        row=s.scalar(select(PublishJob).where(PublishJob.id==job_id).with_for_update())
        if not row:raise HTTPException(404,'Không tìm thấy job')
        if action=='cancel':
            if row.status in ('published','cancelled','stopped'):raise HTTPException(409,'Job đã kết thúc')
            cancel(s,row)
        else:reconcile(s,row)
        return public_job(row)
