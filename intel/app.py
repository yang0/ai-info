import asyncio
import contextlib
import json
import os
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from .models import Settings, utcnow
from .runner import Runner, AGENTS
from .store import Store
from .analysis import make_brief

ROOT = Path(__file__).resolve().parent.parent


def create_app(data_dir=None):
    store = Store(Path(data_dir or os.getenv('AI_INFO_DATA_DIR', ROOT/'data')))
    runner = Runner(store)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        scheduler = asyncio.create_task(runner.scheduler())
        yield
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
        await runner.cancel()

    app = FastAPI(title='AI INFO · 情报作战台', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.runner = store, runner

    @app.middleware('http')
    async def protection(request: Request, call_next):
        if request.url.hostname not in {'localhost', '127.0.0.1', '::1', 'testserver'}:
            return JSONResponse({'detail': '此工作台仅接受本机地址'}, status_code=400)
        if request.url.path.startswith('/api/'):
            origin = request.headers.get('origin')
            if origin and urlparse(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': '跨站请求被拒绝'}, status_code=403)
            if request.url.path not in ('/api/health', '/api/session'):
                supplied = request.headers.get('x-intel-token') or request.cookies.get('intel_session', '')
                if not secrets.compare_digest(supplied, token):
                    return JSONResponse({'detail': '会话已过期，请刷新页面'}, status_code=401)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/session')
    def session():
        response = JSONResponse({'token': token})
        response.set_cookie('intel_session', token, httponly=True, samesite='strict')
        return response

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'version': '0.1.0'}

    @app.get('/api/state')
    def state():
        settings = store.settings()
        snapshot = store.snapshot(settings.dataset_key())
        return {'settings': settings.model_dump(), 'scope': settings.dataset_key(), 'snapshot': snapshot, 'active': runner.active,
                'agents': AGENTS, 'runs': store.runs(), 'events': store.recent_events(),
                'capabilities': {'opencli': bool(shutil.which('opencli')), 'agent_reach': bool(shutil.which('agent-reach')),
                    'llm': bool(os.getenv('OLLAMA_MODEL')), 'llm_model': os.getenv('OLLAMA_MODEL', '')},
                'server_time': utcnow().isoformat(), 'cooldown_until': runner.cooldown_until}

    @app.put('/api/settings')
    async def settings(body: Settings):
        if runner.active:
            raise HTTPException(409, '扫描执行中，请先停止再修改监控配置')
        store.save_settings(body)
        return body.model_dump()

    @app.post('/api/runs', status_code=202)
    async def run():
        try:
            return runner.start()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post('/api/runs/cancel')
    async def cancel():
        await runner.cancel()
        return {'state': 'cancelled'}

    @app.get('/api/events')
    async def events(request: Request):
        try:
            after = max(0, int(request.headers.get('last-event-id', '0')))
        except ValueError:
            after = 0
        async def stream():
            cursor = after
            while not await request.is_disconnected():
                rows = store.events(cursor)
                if not rows:
                    yield ': heartbeat\n\n'
                for row in rows:
                    cursor = row['id']
                    yield f'id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'X-Accel-Buffering': 'no'})

    @app.get('/api/brief')
    def brief():
        snapshot = store.snapshot(store.settings().dataset_key())
        if not snapshot:
            raise HTTPException(404, '请先完成一轮扫描')
        return PlainTextResponse(make_brief(snapshot), headers={
            'Content-Disposition': 'attachment; filename="ai-info-brief.md"'})

    app.mount('/static', StaticFiles(directory=ROOT/'web'), name='static')

    @app.get('/')
    def index():
        return FileResponse(ROOT/'web'/'index.html')

    return app
