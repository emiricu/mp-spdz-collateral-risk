"""FastAPI wrapper: submits jobs to the queue and answers immediately
(202) -- never waits for the MPC run itself, which is the whole point of
moving off the old synchronous /calcola. See node/README.md for the
request/response shapes and a curl example."""
import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from rq import Queue
import redis

from . import config, store, onchain_direct

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('node.main')

app = FastAPI(title='crypto-collateral-risk MPC node', version='1.0')

_redis = redis.from_url(config.REDIS_URL)
_queue = Queue('default', connection=_redis)


class CalcolaRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    callback_url: str = Field(..., min_length=1)
    existing_debt_usd: int
    # Required, no default, same treatment as request_id/port_base: the
    # orchestrator decides which protocol a given request uses and sends
    # the SAME value to every node for that request -- a node must never
    # fall back to a locally-configured default, since the MPC protocol
    # requires all N parties to run the identical program for one
    # request (see node/README.md's tinier-integration section).
    protocol: str
    # Required, no default: the orchestrator assigns one port_base per
    # request and sends the SAME value to every node for that request --
    # see node/README.md sec:9.6 for why a node must never fall back to
    # a locally-derived/static port here (that's the exact bug this
    # closes: a fresh request's process ending up talking to a stale
    # leftover process from an unrelated earlier request, because the
    # static PORT_BASE was reused before the old process could have
    # possibly still been alive).
    port_base: int
    # requester-only (party 0) fields -- absent/ignored on party 1,
    # required on party 0 (validated below, not via pydantic's own
    # required/optional split, since which fields are required depends
    # on which party THIS node is, not on the request alone)
    wallet_address: str | None = None
    new_loan_usd: int | None = None
    collateral_asset: str | None = None
    collateral_amount: float | None = None
    coingecko_api_key: str | None = None


REQUESTER_ONLY_FIELDS = ('wallet_address', 'new_loan_usd', 'collateral_asset',
                          'collateral_amount')


@app.post('/calcola', status_code=202)
def calcola(body: CalcolaRequest):
    if body.protocol not in config.PROTOCOLS:
        raise HTTPException(
            400, f'protocol sconosciuto: {body.protocol!r} '
                 f'(atteso uno tra {sorted(config.PROTOCOLS)})')
    proto = config.PROTOCOLS[body.protocol]
    binary_path = f'{config.MP_SPDZ_HOME}/{proto["binary"]}'
    sch_path = f'{config.MP_SPDZ_HOME}/Programs/Schedules/{proto["prog_name"]}.sch'
    # Checked HERE, synchronously, before enqueueing -- not left to fail
    # inside the worker after being dequeued: a missing binary/program on
    # this node should never look like a hung/timed-out computation to
    # the caller (see node/README.md's tinier-integration section, Step 2
    # point 3).
    if not os.path.exists(binary_path) or not os.path.exists(sch_path):
        raise HTTPException(
            503, f'protocollo {body.protocol!r} non disponibile su questo nodo '
                 f'(mancante: {binary_path if not os.path.exists(binary_path) else sch_path})')
    if config.IS_REQUESTER:
        missing = [f for f in REQUESTER_ONLY_FIELDS if getattr(body, f) in (None, '')]
        if missing:
            raise HTTPException(
                400, f'party 0 (richiedente) richiede anche: {", ".join(missing)}')
    existing = store.get_status(body.request_id)
    if existing and existing['stato'] in ('in coda', 'in corso'):
        # idempotent resubmission of the same request_id while it's
        # already active: don't enqueue a second job for it.
        return JSONResponse(status_code=202, content=existing)

    party_payload = body.model_dump()
    store.set_status(body.request_id, 'in coda', party_id=config.PARTY_ID)
    _queue.enqueue('node_app.tasks.run_mpc_job', body.request_id,
                    body.callback_url, party_payload,
                    job_timeout=config.MPC_TIMEOUT_SECONDS + 120)
    return {'request_id': body.request_id, 'stato': 'in coda', 'party_id': config.PARTY_ID}


@app.get('/stato/{request_id}')
def stato(request_id: str):
    result = store.get_status(request_id)
    if result is None:
        raise HTTPException(404, 'request_id sconosciuto (mai sottomesso, o scaduto)')
    return result


@app.get('/salute')
def salute():
    own_cert = f'{config.MP_SPDZ_HOME}/Player-Data/P{config.PARTY_ID}.pem'
    # Every OTHER party's cert, not just one fixed peer -- at N=3 this
    # party has two peers, not one (see entrypoint.sh's generate_own_cert).
    peer_ids = [i for i in range(config.N_PLATFORMS) if i != config.PARTY_ID]
    certificati_peer = {
        str(i): os.path.exists(f'{config.MP_SPDZ_HOME}/Player-Data/P{i}.pem')
        for i in peer_ids
    }
    # Reported per protocol, not collapsed into one flag: a node can be
    # healthy for mascot but missing tinier (or vice versa) -- see
    # node/README.md's tinier-integration section, Step 2 point 3.
    protocolli = {
        name: dict(
            binario=os.path.exists(f'{config.MP_SPDZ_HOME}/{p["binary"]}'),
            programma_compilato=os.path.exists(
                f'{config.MP_SPDZ_HOME}/Programs/Schedules/{p["prog_name"]}.sch'),
        )
        for name, p in config.PROTOCOLS.items()
    }
    almeno_un_protocollo_ok = any(
        v['binario'] and v['programma_compilato'] for v in protocolli.values())
    dettagli = {
        'processo': True,
        'redis': store.ping(),
        'protocolli': protocolli,
        'certificato_locale': os.path.exists(own_cert),
        'certificati_peer': certificati_peer,
        'party_id': config.PARTY_ID,
        'n_platforms': config.N_PLATFORMS,
    }
    core_ok = (dettagli['redis'] and almeno_un_protocollo_ok
               and dettagli['certificato_locale'])
    return JSONResponse(status_code=200 if core_ok else 503,
                         content={'ok': core_ok, 'dettagli': dettagli})


class OnchainRequest(BaseModel):
    """Shared shape for /commit and /reveal: policy_version and timestamp_unix
    are orchestrator-assigned round metadata (see submit()'s docstring for
    why that's safe), not something this node computes or could disagree
    on; esito comes only from this node's own completed job, never from
    the caller, for the same reason the old /firma refused to sign blind."""
    request_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=66, max_length=66)  # 0x + 64 hex chars
    policy_version: int
    timestamp_unix: int


def _load_verified_esito(request_id: str, session_id: str):
    """Common guard for /commit and /reveal: refuses to touch the chain for
    a job this node hasn't itself completed and verified -- the direct-
    submission analogue of the old /firma's same check (see its
    docstring, preserved here almost verbatim)."""
    job = store.get_status(request_id)
    if job is None:
        raise HTTPException(404, f'request_id sconosciuto a questo nodo: {request_id}')
    if job.get('stato') != 'completato':
        raise HTTPException(409, f"il job {request_id} non e' 'completato' "
                                   f"(stato attuale: {job.get('stato')!r}) -- non firmo/pubblico "
                                   f"un esito che questo nodo non ha ancora concordato")
    esito_label = job.get('esito')
    esito_code = onchain_direct.ESITO_TO_CODE.get(esito_label)
    if esito_code is None:
        raise HTTPException(500, f'esito del job non riconosciuto: {esito_label!r}')
    return esito_code


def _session_id_bytes(session_id: str) -> bytes:
    return bytes.fromhex(session_id.removeprefix('0x'))


@app.post('/commit')
def commit(body: OnchainRequest):
    """Submits this party's commit hash directly to SolvencyRegistry on
    Sepolia -- this node's own transaction, signed with its own persisted
    key, no relay/orchestrator/oracle in between (see onchain_direct.py's
    module docstring). Generates a fresh random salt locally, stores it
    (keyed by session_id, this node only) for the matching /reveal call,
    and returns the real transaction hash."""
    esito_code = _load_verified_esito(body.request_id, body.session_id)
    session_id = _session_id_bytes(body.session_id)
    salt = os.urandom(32)
    commit_hash = onchain_direct.compute_commit_hash(
        session_id, esito_code, body.policy_version, body.timestamp_unix, salt)
    store.set_salt(body.session_id, salt.hex())
    tx_hash = onchain_direct.submit_commit(session_id, commit_hash)
    return {'party_id': config.PARTY_ID, 'session_id': body.session_id, 'tx_hash': tx_hash}


@app.post('/reveal')
def reveal(body: OnchainRequest):
    """Submits this party's reveal directly to SolvencyRegistry on
    Sepolia, using the salt generated by this node's own prior /commit
    call for the same session -- reverts (via SolvencyRegistry's own
    CommitMismatch check) if that doesn't match, never silently
    regenerates one."""
    esito_code = _load_verified_esito(body.request_id, body.session_id)
    session_id = _session_id_bytes(body.session_id)
    salt_hex = store.get_salt(body.session_id)
    if salt_hex is None:
        raise HTTPException(
            409,
            f'nessun salt locale per session_id={body.session_id} -- /commit non e stato '
            f'ancora chiamato per questa sessione su questo nodo')
    salt = bytes.fromhex(salt_hex)
    tx_hash = onchain_direct.submit_reveal(
        session_id, esito_code, body.policy_version, body.timestamp_unix, salt)
    return {'party_id': config.PARTY_ID, 'session_id': body.session_id, 'tx_hash': tx_hash}
