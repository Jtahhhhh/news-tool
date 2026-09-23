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
    if 'application/json' in request.headers.get('accept', '') or request.url.path.startswith('/script-jobs'):
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(request=request, name='error.html',
                                      context={'detail': exc.detail}, status_code=exc.status_code)


@app.get('/health')
def health():
    with session_scope() as session:
        session.execute(text('SELECT 1'))
        revision = session.execute(text('SELECT version_num FROM alembic_version')).scalar()
        if revision != '0004':
            raise HTTPException(503, 'Migration chưa hoàn tất')
    return {'status': 'ok'}


from app.routes.web import router  # noqa: E402
app.include_router(router)
from app.routes.scripts import router as scripts_router  # noqa: E402
app.include_router(scripts_router)
from app.routes.llm_control import router as llm_control_router  # noqa: E402
app.include_router(llm_control_router)

from fastapi.exceptions import RequestValidationError  # noqa: E402
@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, exc):
    # Never reflect an accidentally pasted secret or the original JSON body.
    return JSONResponse({'detail':[{'loc':e['loc'],'type':e['type'],'msg':e['msg']} for e in exc.errors()]},status_code=422)
