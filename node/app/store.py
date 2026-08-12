"""Job-status store, Redis-backed (same Redis instance RQ already uses --
no separate infra). Deliberately independent of RQ's own job registry:
GET /stato needs custom states ('in coda'/'in corso'/'completato'/
'fallito') and to keep serving the last known result long after RQ would
have expired the job's internal bookkeeping."""
import json
import time

import redis

from . import config

_redis = redis.from_url(config.REDIS_URL, decode_responses=True)


def _key(request_id):
    return f'job:{request_id}'


def set_status(request_id, stato, **fields):
    payload = {'request_id': request_id, 'stato': stato,
               'aggiornato_il': time.time(), **fields}
    _redis.set(_key(request_id), json.dumps(payload), ex=config.JOB_TTL_SECONDS)
    return payload


def get_status(request_id):
    raw = _redis.get(_key(request_id))
    return json.loads(raw) if raw else None


def ping():
    try:
        return bool(_redis.ping())
    except redis.RedisError:
        return False
