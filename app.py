"""Administrator-only local Media Forge API and dashboard."""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from core import BASE, DATA, Config, Inventory, Jellyfin, Resources, Store, matching_rules
from worker import Worker
from qbittorrent import TorrentCoordinator, TorrentHold

config = Config()
store = Store()
stop = threading.Event()
jellyfin = Jellyfin(config)
inventory = Inventory(config, store, jellyfin, stop)
resources = Resources(config, inventory, jellyfin, stop)
worker = Worker(config, store, inventory, resources, jellyfin, stop)
worker.torrents = TorrentCoordinator()
sessions, login_attempts = {}, {}
auth_lock = threading.RLock()
threads = []

handler = RotatingFileHandler(DATA / 'server.log', maxBytes=4_000_000, backupCount=3, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_):
    stop.clear()
    for name, target in [('inventory', inventory.loop), ('resources', resources.loop), ('worker', worker.loop)]:
        thread = threading.Thread(target=target, name=name, daemon=True)
        threads.append(thread)
        thread.start()
    store.event('Media Forge started; automatic encoding is ' + ('enabled' if config.get()['auto_queue'] else 'off'))
    yield
    stop.set()
    inventory.wake.set()
    for thread in threads:
        thread.join(timeout=20)


app = FastAPI(title='Media Forge', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
public_host = urlparse(config.get()['public_url']).hostname
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', public_host, 'testserver'])


def allowed_origin(origin, bridge=False):
    public = urlparse(config.get()['public_url'])
    allowed = {f'{public.scheme}://{public.netloc}', 'http://127.0.0.1:5056', 'http://localhost:5056'}
    if bridge:
        allowed |= {'http://localhost:8096', 'http://127.0.0.1:8096'}
    return origin in allowed


@app.middleware('http')
async def security(request: Request, call_next):
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        is_bridge = request.url.path == '/forge/auth/bridge'
        origin = request.headers.get('origin')
        if origin and not allowed_origin(origin, is_bridge):
            return JSONResponse({'detail': 'Origin not allowed'}, status_code=403)
        if is_bridge:
            if not origin or not allowed_origin(origin, True):
                return JSONResponse({'detail': 'Open Media Forge from Jellyfin'}, status_code=403)
        elif request.headers.get('x-forge-request') != '1':
            return JSONResponse({'detail': 'Missing request protection header'}, status_code=403)
        if int(request.headers.get('content-length', '0')) > 150_000:
            return JSONResponse({'detail': 'Request too large'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
    return response


def check_admin(token):
    try:
        result = jellyfin.request('GET', '/Users/Me', token=token)
        if not result.ok:
            raise HTTPException(401, 'Jellyfin session expired; sign in again')
        user = result.json()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'Jellyfin is unavailable; try again after it starts')
    if not user.get('Policy', {}).get('IsAdministrator') or user.get('Policy', {}).get('IsDisabled'):
        raise HTTPException(403, 'A Jellyfin administrator account is required')
    return user


def admin(request: Request):
    cookie = request.cookies.get('forge_session', '')
    digest = hashlib.sha256(cookie.encode()).hexdigest()
    with auth_lock:
        session = sessions.get(digest)
    if not session or session['expires'] < time.time():
        raise HTTPException(401, 'Sign in with your Jellyfin administrator account')
    # Recheck admin privileges frequently; fail closed if Jellyfin is unavailable.
    if time.time() - session['checked'] > 15:
        user = check_admin(session['token'])
        with auth_lock:
            session['checked'] = time.time()
            session['name'] = user['Name']
    return session


def issue_session(token, user, request, redirect=False):
    cookie = secrets.token_urlsafe(48)
    with auth_lock:
        for key in list(sessions):
            if sessions[key]['expires'] < time.time():
                del sessions[key]
        sessions[hashlib.sha256(cookie.encode()).hexdigest()] = {
            'token': token, 'name': user['Name'], 'expires': time.time() + 8 * 3600, 'checked': time.time()}
    response = RedirectResponse('/forge/', status_code=303) if redirect else JSONResponse({'name': user['Name']})
    response.set_cookie('forge_session', cookie, max_age=8 * 3600, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path='/forge/')
    return response


def rate_limit(request):
    ip = request.client.host if request.client else 'local'
    if ip in ('127.0.0.1', '::1') and request.headers.get('cf-connecting-ip'):
        ip = request.headers['cf-connecting-ip']
    with auth_lock:
        attempts = [t for t in login_attempts.get(ip, []) if t > time.time() - 60]
        if len(attempts) >= 10:
            raise HTTPException(429, 'Too many sign-in attempts; wait one minute')
        login_attempts[ip] = [*attempts, time.time()]


@app.get('/healthz')
def health():
    return {'ok': True, 'service': 'Media Forge'}


@app.get('/')
def root():
    return RedirectResponse('/forge/')


@app.get('/forge')
def redirect():
    return RedirectResponse('/forge/')


@app.get('/forge/')
def page():
    return FileResponse(BASE / 'static/index.html')


@app.post('/forge/api/login')
def login(body: dict, request: Request):
    rate_limit(request)
    name, password = body.get('username'), body.get('password')
    if not isinstance(name, str) or not isinstance(password, str) or len(name) > 200 or len(password) > 1024:
        raise HTTPException(400, 'Enter a Jellyfin username and password')
    try:
        response = jellyfin.request('POST', '/Users/AuthenticateByName', json={'Username': name, 'Pw': password})
    except Exception:
        raise HTTPException(503, 'Cannot reach Jellyfin')
    if not response.ok:
        raise HTTPException(401, 'Jellyfin sign-in failed')
    result = response.json()
    token = result.get('AccessToken')
    if not token:
        raise HTTPException(401, 'Jellyfin did not issue a session')
    user = check_admin(token)
    return issue_session(token, user, request)


@app.post('/forge/auth/bridge')
async def bridge(request: Request):
    rate_limit(request)
    raw = await request.body()
    if len(raw) > 16000:
        raise HTTPException(400, 'Invalid session')
    token = parse_qs(raw.decode('utf-8')).get('token', [''])[0]
    if not token or len(token) > 4096:
        return RedirectResponse('/forge/', status_code=303)
    # Network checks run outside the event loop.
    from starlette.concurrency import run_in_threadpool
    user = await run_in_threadpool(check_admin, token)
    return issue_session(token, user, request, redirect=True)


@app.post('/forge/api/logout')
def logout(request: Request):
    digest = hashlib.sha256(request.cookies.get('forge_session', '').encode()).hexdigest()
    with auth_lock:
        sessions.pop(digest, None)
    response = JSONResponse({'ok': True})
    response.delete_cookie('forge_session', path='/forge/')
    return response


@app.get('/forge/api/me')
def me(session=Depends(admin)):
    return {'name': session['name']}


@app.get('/forge/api/state')
def state(_=Depends(admin)):
    c = config.get()
    counts = {'indexed': 0, 'flagged': 0, 'unreadable': 0, 'completed': 0, 'queued': 0, 'failed': 0, 'bytes_saved': 0}
    for row in store.rows('SELECT info,error FROM media WHERE present=1'):
        counts['indexed'] += 1
        counts['unreadable'] += bool(row['error'])
        if row['info'] and matching_rules(json.loads(row['info']), c['rules']):
            counts['flagged'] += 1
    for row in store.rows('SELECT state,original_size,output_size FROM jobs'):
        counts['completed'] += row['state'] == 'completed'
        counts['queued'] += row['state'] in ('queued', 'waiting', 'encoding', 'validating', 'committing')
        counts['failed'] += row['state'] == 'failed'
        if row['state'] == 'completed':
            counts['bytes_saved'] += (row['original_size'] or 0) - (row['output_size'] or 0)
    roots = [{**r, 'online': Path(r['path']).is_dir()} for r in inventory.roots]
    return {'counts': counts, 'inventory': inventory.status, 'resources': resources.value,
            'encoder': worker.encoder, 'roots': roots, 'queue_paused': c['queue_paused'], 'auto_queue': c['auto_queue'],
            'output': worker.options(), 'rules': c['rules'], 'current': worker.current,
            'public_url': c['public_url'], 'now': time.time()}


@app.get('/forge/api/media')
def media(q: str = '', library: str = '', flagged: bool = False, offset: int = 0, limit: int = 50, _=Depends(admin)):
    if len(q) > 300 or len(library) > 200 or offset < 0 or not 1 <= limit <= 200:
        raise HTTPException(400, 'Invalid filter')
    clauses, args = ['present=1'], []
    if q:
        clauses.append('path LIKE ?')
        args.append('%' + q + '%')
    if library:
        clauses.append('library=?')
        args.append(library)
    rows = store.rows('SELECT * FROM media WHERE ' + ' AND '.join(clauses) + ' ORDER BY path COLLATE NOCASE', args)
    rules = config.get()['rules']
    result = []
    jobs_by_media = {}
    for job in store.rows('SELECT media_id,id,state FROM jobs ORDER BY created'):
        jobs_by_media[job['media_id']] = {'id': job['id'], 'state': job['state']}
    for row in rows:
        info = json.loads(row['info']) if row['info'] else None
        hits = matching_rules(info, rules) if info else []
        if flagged and not hits:
            continue
        blocked = None
        if info:
            try:
                worker.check_supported(info, json.loads(row['probe']), worker.options())
            except ValueError as exc:
                blocked = str(exc)
        result.append({'id': row['id'], 'name': Path(row['path']).name, 'path': row['path'], 'library': row['library'],
                       'size': row['size'], 'info': info, 'error': row['error'], 'rules': hits, 'blocked': blocked,
                       'job': jobs_by_media.get(row['id']),
                       'estimated_output': worker.estimate(row, worker.options()) if info else None})
    return {'items': result[offset:offset + limit], 'total': len(result), 'offset': offset, 'limit': limit}


@app.get('/forge/api/media/{media_id}')
def media_details(media_id: int, _=Depends(admin)):
    row = store.one('SELECT * FROM media WHERE id=?', (media_id,))
    if not row:
        raise HTTPException(404, 'Media not found')
    return {**row, 'probe': json.loads(row['probe']) if row['probe'] else None,
            'info': json.loads(row['info']) if row['info'] else None}


@app.post('/forge/api/scan')
def scan(_=Depends(admin)):
    inventory.request_scan()
    return {'ok': True}


@app.get('/forge/api/jobs')
def jobs(_=Depends(admin)):
    rows = store.rows('SELECT * FROM jobs ORDER BY created DESC LIMIT 300')
    for row in rows:
        row['name'] = Path(row['path']).name
        row['options'] = json.loads(row['options'])
        row['validation'] = json.loads(row['validation']) if row['validation'] else None
    return rows


@app.post('/forge/api/jobs')
def enqueue(body: dict, _=Depends(admin)):
    ids = body.get('media_ids')
    if not isinstance(ids, list) or not ids or len(ids) > 200 or not all(type(x) is int for x in ids):
        raise HTTPException(400, 'Select between 1 and 200 media files')
    results = []
    for media_id in set(ids):
        try:
            results.append({'media_id': media_id, 'job_id': worker.enqueue(media_id)})
        except (ValueError, OSError) as exc:
            results.append({'media_id': media_id, 'error': str(exc)})
    return {'results': results}


@app.post('/forge/api/jobs/{job_id}/{action}')
def job_action(job_id: str, action: str, _=Depends(admin)):
    try:
        if action == 'cancel':
            worker.cancel(job_id)
            return {'ok': True}
        if action == 'retry':
            return {'job_id': worker.retry(job_id)}
        raise ValueError('Unknown action')
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc))


@app.get('/forge/api/jobs/{job_id}/log')
def job_log(job_id: str, _=Depends(admin)):
    if not store.one('SELECT id FROM jobs WHERE id=?', (job_id,)):
        raise HTTPException(404, 'Job not found')
    logs = {}
    for phase in ('encode', 'validate'):
        p = DATA / f'{job_id}-{phase}.log'
        if p.exists():
            with p.open('rb') as stream:
                stream.seek(max(0, p.stat().st_size - 20000))
                logs[phase] = stream.read().decode('utf-8', 'replace')
    return logs


@app.get('/forge/api/settings')
def settings(_=Depends(admin)):
    return {k: v for k, v in config.get().items() if k not in ('ffmpeg', 'ffprobe', 'jellyfin_data', 'host', 'port')}


@app.put('/forge/api/settings')
def save_settings(body: dict, _=Depends(admin)):
    try:
        config.update(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    store.event('Administrator updated encoder / queue settings')
    return {'ok': True}


@app.post('/forge/api/encoder-test')
def encoder_test(_=Depends(admin)):
    if worker.current:
        raise HTTPException(409, 'Wait for the current job to finish')
    worker.test_encoder()
    return worker.encoder


@app.get('/forge/api/qbittorrent')
def torrent_settings(_=Depends(admin)):
    try:
        return worker.torrents.credentials.status()
    except TorrentHold as exc:
        raise HTTPException(503, str(exc))


@app.put('/forge/api/qbittorrent')
def save_torrent_settings(body: dict, _=Depends(admin)):
    if worker.current:
        raise HTTPException(409, 'Wait for the current job to finish before changing the connection')
    try:
        result = worker.torrents.configure(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except (TorrentHold, RuntimeError) as exc:
        raise HTTPException(503, str(exc))
    store.event('Administrator updated the local qBittorrent connection')
    return result


@app.get('/forge/api/events')
def events(_=Depends(admin)):
    return store.rows('SELECT * FROM events ORDER BY id DESC LIMIT 100')


app.mount('/forge/static', StaticFiles(directory=BASE / 'static'), name='static')
