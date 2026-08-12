"""POST the result to callback_url, with bounded exponential backoff.
Runs synchronously inside the RQ job (concurrency=1 means it blocks the
next queued job while retrying) -- an accepted, documented tradeoff for
a single-worker-per-node prototype; see node/README.md. The result is
never lost even if every retry fails: it's already in the status store
(GET /stato/<request_id>) before the first callback attempt."""
import logging
import time

import requests

from . import config

log = logging.getLogger('node.callback')


def send_with_retry(callback_url, payload):
    for attempt in range(1, config.CALLBACK_MAX_RETRIES + 1):
        try:
            r = requests.post(callback_url, json=payload,
                               timeout=config.CALLBACK_TIMEOUT_SECONDS)
            if r.status_code < 300:
                log.info('callback ok (tentativo %d/%d) -> %s',
                         attempt, config.CALLBACK_MAX_RETRIES, callback_url)
                return True
            log.warning('callback risposta %s (tentativo %d/%d) -> %s',
                        r.status_code, attempt, config.CALLBACK_MAX_RETRIES, callback_url)
        except requests.RequestException as exc:
            log.warning('callback fallita (tentativo %d/%d) -> %s: %s',
                        attempt, config.CALLBACK_MAX_RETRIES, callback_url, exc)
        if attempt < config.CALLBACK_MAX_RETRIES:
            delay = config.CALLBACK_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            time.sleep(delay)
    log.error('callback non riuscita dopo %d tentativi -> %s -- il risultato '
              'resta disponibile via GET /stato/%s',
              config.CALLBACK_MAX_RETRIES, callback_url, payload.get('request_id'))
    return False
