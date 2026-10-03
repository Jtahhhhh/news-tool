import secrets
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from app.database import session_scope

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title='News Tool', version='0.1.0')
templates = Jinja2Templates(directory=BASE_DIR / 'templates')
app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')


@app.middleware('http')
async def csrf_protection(request: Request, call_next):
    token = request.cookies.get('csrf_token') or secrets.token_urlsafe(32)
    request.state.csrf_token = token
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        supplied = request.headers.get('x-csrf-token', '')
        if 'application/x-www-form-urlencoded' in request.headers.get('content-type', ''):
            # Replay the cached body to the route after middleware validates the form.
            from urllib.parse import parse_qs
            body = await request.body()
            supplied = parse_qs(body.decode()).get('csrf_token', [supplied])[0]
        if not request.cookies.get('csrf_token') or not secrets.compare_digest(token, supplied):
            return HTMLResponse('CSRF token không hợp lệ. Tải lại trang rồi thử lại.', status_code=403)
    response = await call_next(request)
    if not request.cookies.get('csrf_token'):
        response.set_cookie('csrf_token', token, httponly=True, samesite='strict', secure=request.url.scheme == 'https')
    return response


@app.exception_handler(HTTPException)
async def error_page(request, exc):
    if 'application/json' in request.headers.get('accept', '') or request.url.path.startswith(('/api/','/script-jobs','/video-jobs','/publish-jobs','/assets','/videos/')):
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(request=request, name='error.html',
                                      context={'detail': exc.detail}, status_code=exc.status_code)


@app.get('/health')
def health():
    with session_scope() as session:
        session.execute(text('SELECT 1'))
        revision = session.execute(text('SELECT version_num FROM alembic_version')).scalar()
        if revision != '0009':
            raise HTTPException(503, 'Migration chưa hoàn tất')
    return {'status': 'ok'}


from app.routes.web import router  # noqa: E402
app.include_router(router)
from app.routes.scripts import router as scripts_router  # noqa: E402
app.include_router(scripts_router)
from app.routes.llm_control import router as llm_control_router  # noqa: E402
app.include_router(llm_control_router)
from app.routes.video import router as video_router  # noqa: E402
app.include_router(video_router)
from app.routes.publishing import router as publishing_router  # noqa: E402
app.include_router(publishing_router)


@app.middleware('http')
async def publishing_privacy_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(('/tiktok', '/publishing', '/publish-jobs')):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
    return response

from fastapi.exceptions import RequestValidationError  # noqa: E402
@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, exc):
    # Never reflect an accidentally pasted secret or the original JSON body.
    return JSONResponse({'detail':[{'loc':e['loc'],'type':e['type'],'msg':e['msg']} for e in exc.errors()]},status_code=422)

from app.routes.video_editor import router as video_editor_router
app.include_router(video_editor_router)

from app.routes.dashboard import router as dashboard_router
from app.dashboard_auth import router as auth_router, protect
app.include_router(dashboard_router)
app.include_router(auth_router)
app.middleware('http')(protect)

from fastapi.middleware.cors import CORSMiddleware
import os
origins = [value.strip() for value in os.getenv('CORS_ORIGINS', '').split(',') if value.strip()]
if origins:
    if '*' in origins:
        raise RuntimeError('CORS_ORIGINS must contain explicit origins')
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=['GET', 'POST', 'PATCH', 'DELETE'], allow_headers=['Content-Type', 'X-CSRF-Token'])

frontend = BASE_DIR.parent / 'frontend' / 'dist'
if frontend.is_dir():
    app.mount('/dashboard/assets', StaticFiles(directory=frontend / 'assets'), name='dashboard-assets')

    @app.get('/dashboard')
    @app.get('/dashboard/{path:path}')
    def dashboard_spa(path: str = ''):
        from fastapi.responses import FileResponse
        return FileResponse(frontend / 'index.html')
