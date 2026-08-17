"""The single RQ job type this node runs. One job = one party's side of
one MPC computation for one request_id."""
import logging

from . import config, mpc_runner, store, callback

log = logging.getLogger('node.tasks')

RAW_LOG_DIR = f'{config.MP_SPDZ_HOME}/node_logs'


def run_mpc_job(request_id, callback_url, party_payload):
    store.set_status(request_id, 'in corso', party_id=config.PARTY_ID)
    try:
        mpc_runner.write_own_input(party_payload)
        stdout, elapsed = mpc_runner.run_party(
            request_id, party_payload['port_base'], raw_log_dir=RAW_LOG_DIR)
        result = mpc_runner.parse_result(stdout)
    except mpc_runner.MpcRunError as exc:
        log.error('run %s fallito: %s', request_id, exc)
        payload = store.set_status(request_id, 'fallito', party_id=config.PARTY_ID,
                                    errore=str(exc))
        callback.send_with_retry(callback_url, payload)
        return payload
    except Exception as exc:  # noqa: BLE001 -- any unexpected crash still owes a callback
        log.exception('run %s: errore inatteso', request_id)
        payload = store.set_status(request_id, 'fallito', party_id=config.PARTY_ID,
                                    errore=f'errore inatteso: {exc}')
        callback.send_with_retry(callback_url, payload)
        return payload

    payload = store.set_status(
        request_id, 'completato', party_id=config.PARTY_ID,
        durata_secondi=round(elapsed, 2), **result)
    callback.send_with_retry(callback_url, payload)
    return payload
