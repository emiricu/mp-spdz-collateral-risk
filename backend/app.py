"""Minimal orchestrator + demo frontend host for the crypto-collateral-risk
MPC network. Deliberately small: this exists to let a browser trigger a
real end-to-end quote (submit the SAME request_id to all N_PLATFORMS
nodes in parallel, per node/README.md's own note to whoever builds this
backend), not to be a production loan-origination system. Session-cookie
login, per-IP + global rate limiting, and a static "cryptolike" frontend
are the only concessions to running this on the open internet.
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

# N_PLATFORMS drives everything below it: how many existing_debt_p<i>
# fields /api/preventivo requires, how many NODE<i>_URL nodes get the
# parallel /calcola submission, and how many callbacks /api/callback
# waits for before finalizing a quote -- change this (and deploy the
# matching number of node containers/NODE<i>_URL env vars) rather than
# hardcoding a party count anywhere else in this file.
N_PLATFORMS = int(os.environ.get('N_PLATFORMS', '3'))
NODE_URLS = [
    os.environ.get(f'NODE{i}_URL', f'http://node{i}-api:8000')
    for i in range(N_PLATFORMS)
]
CALLBACK_BASE_URL = os.environ.get('CALLBACK_BASE_URL', 'http://backend:9091')
SESSION_SECRET = os.environ['SESSION_SECRET']
ADMIN_PASSWORD_HASH = os.environ['ADMIN_PASSWORD_HASH']  # sha256 hex
REDIS_URL = os.environ.get('REDIS_URL', 'redis://backend-redis:6379/0')

RATE_LIMIT_PER_IP = int(os.environ.get('RATE_LIMIT_PER_IP', '3'))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get('RATE_LIMIT_WINDOW_SECONDS', str(3600)))
MAX_CONCURRENT_QUOTES = int(os.environ.get('MAX_CONCURRENT_QUOTES', '3'))
QUOTE_TTL_SECONDS = int(os.environ.get('QUOTE_TTL_SECONDS', str(24 * 3600)))
# A node that dies mid-computation (killed, crashed, network partition)
# never calls back at all -- without this, a quote missing even one
# party's callback would stay 'in corso' forever. node/app/config.py's
# own MPC_TIMEOUT_SECONDS default is 600s; this adds a margin on top for
# callback delivery/retry (see node/app/callback.py's own backoff) before
# the ORCHESTRATOR gives up waiting and reports the missing party/ies
# explicitly, rather than silently finalizing on whichever subset did
# respond.
QUOTE_TIMEOUT_SECONDS = int(os.environ.get('QUOTE_TIMEOUT_SECONDS', str(700)))

# Per-request MP-SPDZ port allocation (node/README.md sec:9.5/9.6): every
# party used to be invoked with the SAME static PORT_BASE for every
# request. Under back-to-back load a node's single-concurrency worker
# can still be finishing a PREVIOUS request's mascot-party.x process
# (observed hanging up to ~226s past its peer's own crash) when a fresh
# request starts on another node -- that fresh process then binds/dials
# the very same ports and can end up completing a protocol run with the
# stale leftover process instead of its own intended peers, since
# mascot-party.x's own coordination has no concept of request identity,
# only a fixed port per party. A ring buffer of PORT_POOL_SIZE slots
# means a port_base is only ever reused after PORT_POOL_SIZE other
# requests have been assigned one; PORT_SLOT_MIN_AGE_SECONDS is the hard
# backstop that makes the guarantee absolute rather than merely
# probabilistic -- reuse within that window is refused outright (503),
# not silently allowed, regardless of how fast requests are arriving.
PORT_POOL_BASE = int(os.environ.get('PORT_POOL_BASE', '20000'))
PORT_POOL_SIZE = int(os.environ.get('PORT_POOL_SIZE', '50'))
PORT_POOL_STRIDE = int(os.environ.get('PORT_POOL_STRIDE', '10'))
# Must exceed the longest any single party's mascot-party.x process can
# possibly stay alive. That's a hard ceiling, not an estimate: node/app/
# mpc_runner.py's subprocess.run(timeout=config.MPC_TIMEOUT_SECONDS)
# forcibly kills the child at 600s by default, on every node,
# independent of what any OTHER party is doing. This adds a margin on
# top for scheduling/callback delivery.
PORT_SLOT_MIN_AGE_SECONDS = int(os.environ.get('PORT_SLOT_MIN_AGE_SECONDS', '900'))

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
                                  f'tra qualche minuto -- ogni calcolo richiede tutti e '
                                  f'{N_PLATFORMS} i nodi dedicati, non e\' istantaneo per design')


def _allocate_port_base():
    """One dedicated MP-SPDZ port_base per request, atomically assigned
    (Redis INCR is safe under concurrent /api/preventivo calls). Never
    hands out a slot still within PORT_SLOT_MIN_AGE_SECONDS of its
    previous assignment -- see the constants' own docstring above for
    why this needs to be a hard refusal, not just a big-enough pool."""
    slot = _redis.incr('port_pool_counter') % PORT_POOL_SIZE
    key = f'port_pool_slot:{slot}'
    last_used = _redis.get(key)
    now = time.time()
    if last_used and now - float(last_used) < PORT_SLOT_MIN_AGE_SECONDS:
        raise HTTPException(
            503, 'sistema al momento non puo\' allocare in sicurezza una porta dedicata '
                 'per una nuova richiesta, riprova tra qualche minuto')
    _redis.set(key, now, ex=PORT_SLOT_MIN_AGE_SECONDS * 2)
    return PORT_POOL_BASE + slot * PORT_POOL_STRIDE


# both production-mode reveal shapes (N==2's fascia, N>=3's exact figure
# -- see crypto_collateral_risk.mpc's own N_PLATFORMS==2 branch and
# node/app/mpc_runner.py's parse_result) are covered here so this stays
# correct regardless of which one is actually present in a given party's
# callback payload.
RESULT_FIELDS = ('esito', 'risk_score', 'max_ltv_bps', 'liq_threshold_bps',
                  'fascia_esposizione', 'esposizione_totale_usd')


def _finalize_if_all_reported(q):
    """Never marks a quote 'completato' until EVERY party has reported --
    the previous version did so as soon as the FIRST party's callback
    said so, without waiting for the others or checking whether they
    agreed. At N parties, a single party's report is not enough to trust
    a SUCCESS: this waits for all N, then cross-checks that they all
    agree on esito/risk_score (not just a pairwise comparison) before
    trusting any of them -- a genuine disagreement is reported as its
    own explicit 'incoerente' state, never silently resolved by picking
    a majority or whichever arrived first.

    A FAILURE, in contrast, is reported as soon as the first party
    reports one, without waiting for the rest: MP-SPDZ is a synchronous
    protocol, so one party failing means the whole run failed for
    everyone, and a party that was killed or crashed outright may never
    call back at all -- waiting for it would just mean sitting on an
    already-known failure until QUOTE_TIMEOUT_SECONDS, instead of
    reporting it the moment it's known (reproduced directly: a killed
    worker's peers detect and report the break in under a second)."""
    n = q.get('n_platforms', N_PLATFORMS)
    parties = [q.get(f'party{i}') for i in range(n)]
    reported = [p for p in parties if p is not None]

    failed = [p for p in reported if p.get('stato') != 'completato']
    if failed:
        q['stato'] = 'fallito'
        q['errore'] = '; '.join(
            f"party {p.get('party_id')}: {p.get('errore', 'errore sconosciuto dal nodo')}"
            for p in failed)
        return

    if len(reported) < n:
        return  # still waiting on at least one party -- stays 'in corso'

    esiti = {p.get('esito') for p in parties}
    punteggi = {p.get('risk_score') for p in parties}
    if len(esiti) > 1 or len(punteggi) > 1:
        q['stato'] = 'incoerente'
        q['errore'] = (f"i {n} nodi non concordano sul risultato -- esiti="
                        f"{sorted(str(e) for e in esiti)} risk_score="
                        f"{sorted(str(s) for s in punteggi)}: possibile bug del "
                        f"protocollo o dati di input divergenti, da investigare "
                        f"manualmente, non risolvibile automaticamente")
        return

    q['stato'] = 'completato'
    for k in RESULT_FIELDS:
        for p in parties:
            if p.get(k) is not None:
                q[k] = p[k]
                break


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
        existing_debts = [int(body[f'existing_debt_p{i}']) for i in range(N_PLATFORMS)]
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(400, f'input non valido: {exc}')
    for i, val in enumerate(existing_debts):
        if not (0 <= val <= 1_000_000_000):
            raise HTTPException(400, f'existing_debt_p{i} fuori range')
    if not (0 <= new_loan_usd <= 1_000_000_000):
        raise HTTPException(400, 'new_loan_usd fuori range')
    if not (0 < collateral_amount <= 1_000_000):
        raise HTTPException(400, 'collateral_amount fuori range')

    request_id = str(uuid.uuid4())
    port_base = _allocate_port_base()

    callback_url = f'{CALLBACK_BASE_URL}/api/callback'
    # party 0 (the requester) carries every field the .mpc program needs
    # to derive publicly, everyone else contributes only their own debt
    # figure -- same split node/app/main.py's REQUESTER_ONLY_FIELDS
    # already enforces on the node side, mirrored here for N parties.
    # port_base: every node for THIS request must use the identical
    # value (see node/README.md sec:9.6) -- assigned once, here, and
    # handed to all N nodes below, never derived locally by a node.
    payloads = [{'request_id': request_id, 'callback_url': callback_url,
                 'existing_debt_usd': existing_debts[i], 'port_base': port_base}
                for i in range(N_PLATFORMS)]
    payloads[0].update({
        'wallet_address': wallet_address, 'new_loan_usd': new_loan_usd,
        'collateral_asset': collateral_asset, 'collateral_amount': collateral_amount,
    })

    set_quote(request_id, {
        'request_id': request_id, 'stato': 'in coda', 'submitted_at': time.time(),
        'n_platforms': N_PLATFORMS,
        **{f'party{i}': None for i in range(N_PLATFORMS)},
    })

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            responses = await asyncio.gather(*(
                client.post(f'{NODE_URLS[i]}/calcola', json=payloads[i])
                for i in range(N_PLATFORMS)
            ))
            for r in responses:
                r.raise_for_status()
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
    if q['stato'] in ('in coda', 'in corso'):
        elapsed = time.time() - q['submitted_at']
        if elapsed > QUOTE_TIMEOUT_SECONDS:
            n = q.get('n_platforms', N_PLATFORMS)
            missing = [i for i in range(n) if q.get(f'party{i}') is None]
            q['stato'] = 'fallito'
            q['errore'] = (f'timeout dopo {elapsed:.0f}s in attesa dei nodi '
                            f'{missing} (budget {QUOTE_TIMEOUT_SECONDS}s) -- non ha/hanno '
                            f'mai risposto, non si procede con una risposta parziale')
            set_quote(request_id, q)
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
    _finalize_if_all_reported(q)
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
