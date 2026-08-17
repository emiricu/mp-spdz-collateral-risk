"""Minimal orchestrator + demo frontend host for the crypto-collateral-risk
MPC network. Deliberately small: this exists to let a browser trigger a
real end-to-end quote (submit the SAME request_id to both nodes in
parallel, per node/README.md's own note to whoever builds this backend),
not to be a production loan-origination system. Session-cookie login,
per-IP + global rate limiting, and a static "cryptolike" frontend are the
only concessions to running this on the open internet.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

import httpx
import redis
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('backend')

STATIC_DIR = Path(__file__).parent / 'static'
CONTENT_DIR = Path(__file__).parent / 'content'

NODE0_URL = os.environ.get('NODE0_URL', 'http://node0-api:8000')
NODE1_URL = os.environ.get('NODE1_URL', 'http://node1-api:8000')
CALLBACK_BASE_URL = os.environ.get('CALLBACK_BASE_URL', 'http://backend:9091')
SESSION_SECRET = os.environ['SESSION_SECRET']
ADMIN_PASSWORD_HASH = os.environ['ADMIN_PASSWORD_HASH']  # sha256 hex
REDIS_URL = os.environ.get('REDIS_URL', 'redis://backend-redis:6379/0')

RATE_LIMIT_PER_IP = int(os.environ.get('RATE_LIMIT_PER_IP', '3'))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get('RATE_LIMIT_WINDOW_SECONDS', str(3600)))
MAX_CONCURRENT_QUOTES = int(os.environ.get('MAX_CONCURRENT_QUOTES', '3'))
QUOTE_TTL_SECONDS = int(os.environ.get('QUOTE_TTL_SECONDS', str(24 * 3600)))

DEMO_WALLETS = {
    'clean': '0xclean0000000000000000000000000000000001',
    'borderline': '0xborderline00000000000000000000000003',
    'flagged': '0xflagged000000000000000000000000000002',
}

app = FastAPI(title='crypto-collateral-risk demo')
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=8 * 3600,
                    same_site='lax')

# Redis-backed, not an in-memory dict: this app runs as TWO uvicorn
# processes from the same module (see entrypoint.sh -- 9090 HTTPS for the
# browser, 9091 plain HTTP for node callbacks, since a plaintext client
# hitting an HTTPS-only port fails outright). Two processes never share
# Python memory -- an in-memory dict here silently "forgot" every quote
# by the time its callback arrived on the other process, logged as
# "callback per request_id sconosciuto" -- reproduced directly, not
# assumed. Rate-limit hits stay in-process (in-memory is fine): that
# route is only ever reached via the 9090 process in practice.
_redis = redis.from_url(REDIS_URL, decode_responses=True)
_ip_hits = defaultdict(deque)  # ip -> deque of submission timestamps


def _quote_key(request_id):
    return f'quote:{request_id}'


def get_quote(request_id):
    raw = _redis.get(_quote_key(request_id))
    return json.loads(raw) if raw else None


def set_quote(request_id, quote):
    _redis.set(_quote_key(request_id), json.dumps(quote), ex=QUOTE_TTL_SECONDS)


def count_active_quotes():
    # small demo scale (MAX_CONCURRENT_QUOTES is single digits) -- a scan
    # per submission is fine, not worth a secondary index for this.
    active = 0
    for key in _redis.scan_iter('quote:*'):
        raw = _redis.get(key)
        if raw and json.loads(raw).get('stato') in ('in coda', 'in corso'):
            active += 1
    return active


def _password_ok(password: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(password.encode()).hexdigest(), ADMIN_PASSWORD_HASH)


def _require_login(request: Request):
    if not request.session.get('authed'):
        raise HTTPException(401, 'login richiesto')


def _check_rate_limit(ip: str):
    now = time.time()
    hits = _ip_hits[ip]
    while hits and now - hits[0] > RATE_LIMIT_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= RATE_LIMIT_PER_IP:
        retry_in = int(RATE_LIMIT_WINDOW_SECONDS - (now - hits[0]))
        raise HTTPException(429, f'troppe richieste da questo indirizzo, riprova tra '
                                  f'{max(retry_in, 1)}s (limite: {RATE_LIMIT_PER_IP}/ora)')
    hits.append(now)


def _check_global_capacity():
    if count_active_quotes() >= MAX_CONCURRENT_QUOTES:
        raise HTTPException(503, 'sistema al momento occupato con altre richieste, riprova '
                                  'tra qualche minuto -- ogni calcolo richiede i due nodi '
                                  'seriali dedicati, non e\' istantaneo per design')


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
@app.post('/api/login')
async def login(request: Request):
    body = await request.json()
    if not _password_ok(body.get('password', '')):
        await asyncio.sleep(0.5)  # slow down brute force, cheaply
        raise HTTPException(401, 'password errata')
    request.session['authed'] = True
    return {'ok': True}


@app.post('/api/logout')
async def logout(request: Request):
    request.session.clear()
    return {'ok': True}


@app.get('/api/whoami')
async def whoami(request: Request):
    return {'authed': bool(request.session.get('authed'))}


# ---------------------------------------------------------------------------
# quote submission
# ---------------------------------------------------------------------------
@app.get('/api/wallets')
async def wallets(request: Request):
    _require_login(request)
    return DEMO_WALLETS


@app.post('/api/preventivo')
async def preventivo(request: Request):
    _require_login(request)
    ip = request.client.host if request.client else 'unknown'
    _check_rate_limit(ip)
    _check_global_capacity()

    body = await request.json()
    wallet_key = body.get('wallet', 'clean')
    wallet_address = DEMO_WALLETS.get(wallet_key, DEMO_WALLETS['clean'])
    try:
        new_loan_usd = int(body['new_loan_usd'])
        collateral_asset = str(body.get('collateral_asset', 'BTC'))
        collateral_amount = float(body['collateral_amount'])
        existing_debt_p0 = int(body['existing_debt_p0'])
        existing_debt_p1 = int(body['existing_debt_p1'])
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(400, f'input non valido: {exc}')
    for name, val in (('new_loan_usd', new_loan_usd), ('existing_debt_p0', existing_debt_p0),
                       ('existing_debt_p1', existing_debt_p1)):
        if not (0 <= val <= 1_000_000_000):
            raise HTTPException(400, f'{name} fuori range')
    if not (0 < collateral_amount <= 1_000_000):
        raise HTTPException(400, 'collateral_amount fuori range')

    request_id = str(uuid.uuid4())

    callback_url = f'{CALLBACK_BASE_URL}/api/callback'
    payload0 = {
        'request_id': request_id, 'callback_url': callback_url,
        'wallet_address': wallet_address, 'existing_debt_usd': existing_debt_p0,
        'new_loan_usd': new_loan_usd, 'collateral_asset': collateral_asset,
        'collateral_amount': collateral_amount,
    }
    payload1 = {
        'request_id': request_id, 'callback_url': callback_url,
        'existing_debt_usd': existing_debt_p1,
    }

    set_quote(request_id, {
        'request_id': request_id, 'stato': 'in coda', 'submitted_at': time.time(),
        'party0': None, 'party1': None,
    })

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r0, r1 = await asyncio.gather(
                client.post(f'{NODE0_URL}/calcola', json=payload0),
                client.post(f'{NODE1_URL}/calcola', json=payload1),
            )
            r0.raise_for_status()
            r1.raise_for_status()
        except httpx.HTTPError as exc:
            q = get_quote(request_id)
            q['stato'] = 'fallito'
            q['errore'] = f'sottomissione ai nodi fallita: {exc}'
            set_quote(request_id, q)
            raise HTTPException(502, 'sottomissione ai nodi fallita, riprova') from exc

    q = get_quote(request_id)
    q['stato'] = 'in corso'
    set_quote(request_id, q)
    return {'request_id': request_id, 'stato': 'in corso'}


@app.get('/api/stato/{request_id}')
async def stato(request: Request, request_id: str):
    _require_login(request)
    q = get_quote(request_id)
    if q is None:
        raise HTTPException(404, 'request_id sconosciuto')
    return q


# ---------------------------------------------------------------------------
# node callback -- NOT session-gated (called by the node containers, not a
# browser, over the internal-only 9091 listener); the request_id itself is
# an unguessable uuid4, and this only ever writes into an entry this
# backend created via /api/preventivo.
# ---------------------------------------------------------------------------
@app.post('/api/callback')
async def callback(request: Request):
    body = await request.json()
    request_id = body.get('request_id')
    q = get_quote(request_id)
    if q is None:
        log.warning('callback per request_id sconosciuto: %s', request_id)
        return {'ok': False}
    party_id = body.get('party_id')
    q[f'party{party_id}'] = body
    # both modes reveal identical esito/risk_score to both parties (see
    # crypto_collateral_risk.mpc's decide_production docstring) -- either
    # party's payload is authoritative once present.
    if body.get('stato') == 'completato':
        q['stato'] = 'completato'
        for k in ('esito', 'risk_score', 'max_ltv_bps', 'liq_threshold_bps',
                  'fascia_esposizione'):
            if k in body:
                q[k] = body[k]
    elif q['stato'] != 'completato':
        q['stato'] = 'fallito'
        q['errore'] = body.get('errore', 'errore sconosciuto dal nodo')
    set_quote(request_id, q)
    return {'ok': True}


# ---------------------------------------------------------------------------
# frontend
# ---------------------------------------------------------------------------
app.mount('/assets', StaticFiles(directory=str(STATIC_DIR)), name='assets')
# public, read-only content used by static pages (e.g. /documentazione) --
# deliberately separate from STATIC_DIR: this is prose content meant to be
# edited on its own (add a section = add an object to the JSON), not app
# code or markup.
app.mount('/content', StaticFiles(directory=str(CONTENT_DIR)), name='content')


@app.get('/')
async def home():
    # the site opens on the public documentation, not the login-gated
    # demo -- a visitor should be able to read what this is before ever
    # being asked for a password. /demo is the actual app.
    return FileResponse(STATIC_DIR / 'documentazione.html')


@app.get('/documentazione')
async def documentazione():
    # same page as '/', kept as a stable direct link.
    return FileResponse(STATIC_DIR / 'documentazione.html')


@app.get('/demo')
async def demo():
    # the login-gated app (client-side gate, see static/index.html).
    return FileResponse(STATIC_DIR / 'index.html')
