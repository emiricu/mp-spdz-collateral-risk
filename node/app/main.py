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

from . import config, store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('node.main')

app = FastAPI(title='crypto-collateral-risk MPC node', version='1.0')

_redis = redis.from_url(config.REDIS_URL)
_queue = Queue('default', connection=_redis)


class CalcolaRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    callback_url: str = Field(..., min_length=1)
    existing_debt_usd: int
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
    prog_sch = f'{config.MP_SPDZ_HOME}/Programs/Schedules/{config.PROG_NAME}.sch'
    own_cert = f'{config.MP_SPDZ_HOME}/Player-Data/P{config.PARTY_ID}.pem'
    # Every OTHER party's cert, not just one fixed peer -- at N=3 this
    # party has two peers, not one (see entrypoint.sh's generate_own_cert).
    peer_ids = [i for i in range(config.N_PLATFORMS) if i != config.PARTY_ID]
    certificati_peer = {
        str(i): os.path.exists(f'{config.MP_SPDZ_HOME}/Player-Data/P{i}.pem')
        for i in peer_ids
    }
    dettagli = {
        'processo': True,
        'redis': store.ping(),
        'binario_mascot': os.path.exists(f'{config.MP_SPDZ_HOME}/mascot-party.x'),
        'programma_compilato': os.path.exists(prog_sch),
        'certificato_locale': os.path.exists(own_cert),
        'certificati_peer': certificati_peer,
        'party_id': config.PARTY_ID,
        'n_platforms': config.N_PLATFORMS,
    }
    core_ok = (dettagli['redis'] and dettagli['binario_mascot']
               and dettagli['programma_compilato'] and dettagli['certificato_locale'])
    return JSONResponse(status_code=200 if core_ok else 503,
                         content={'ok': core_ok, 'dettagli': dettagli})
