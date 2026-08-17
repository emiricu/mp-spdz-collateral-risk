#!/usr/bin/env python3
"""Repeats the local-test success-path scenario N times, full stack
restart between repetitions (down -v, up, wait for /salute on both
nodes), to check whether the single-sample 34.07s/34.48s timing already
observed once is representative or a lucky sample -- same principle
already applied to the mascot/mama controlled re-test in
results_retest_controllato/, now applied to the Docker node prototype
itself.

Writes local-test/results/run<N>.json, one file per repetition, not just
an aggregate -- so a reader can see the raw spread, not a hidden average.

Usage: python3 local-test/run_repeated_success.py --reps 5
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
RESULTS_DIR = HERE / 'results'

WALLET = '0xclean0000000000000000000000000000000001'
# port_base: required since node/README.md sec:9.6 (per-request MP-SPDZ
# ports, see backend/app.py's _allocate_port_base) -- this harness has
# no orchestrator assigning one, so it just reuses each node's own
# static PORT_BASE (see docker-compose.yml), fine for a single-request-
# at-a-time local dev loop that always restarts the stack between reps.
SCENARIO_P0 = {
    'wallet_address': WALLET,
    'existing_debt_usd': 10000,
    'new_loan_usd': 50000,
    'collateral_asset': 'BTC',
    'collateral_amount': 3.5,
    'port_base': 5000,
}
SCENARIO_P1 = {'existing_debt_usd': 10000, 'port_base': 5000}

NODE0 = 'http://localhost:8000'
NODE1 = 'http://localhost:8001'
FAKE_BACKEND = 'http://localhost:9000'


def sh(cmd, **kw):
    print(f'  $ {cmd}', flush=True)
    return subprocess.run(cmd, shell=True, cwd=HERE, check=True, **kw)


def http_json(url, data=None, timeout=15):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method='POST' if data else 'GET',
                                  headers={'Content-Type': 'application/json'} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def wait_for_health(url, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            d = http_json(f'{url}/salute')
            if d.get('ok'):
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f'{url}/salute never became healthy within {timeout}s')


def wait_for_status(url, request_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            d = http_json(f'{url}/stato/{request_id}')
            if d.get('stato') in ('completato', 'fallito'):
                return d
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f'{url}/stato/{request_id} never reached a terminal state within {timeout}s')


def run_once(rep):
    print(f'\n##### rep {rep}: stack restart #####', flush=True)
    sh('docker compose down -v')
    sh('docker compose up -d')

    print('  waiting for both nodes to report /salute ok...', flush=True)
    wait_for_health(NODE0)
    wait_for_health(NODE1)

    request_id = f'repeat-{rep}-{int(time.time())}'
    print(f'  submitting {request_id} to both nodes', flush=True)
    r0 = http_json(f'{NODE0}/calcola', {'request_id': request_id,
                    'callback_url': 'http://fake-backend:9000/callback-test', **SCENARIO_P0})
    r1 = http_json(f'{NODE1}/calcola', {'request_id': request_id,
                    'callback_url': 'http://fake-backend:9000/callback-test', **SCENARIO_P1})
    assert r0['stato'] == 'in coda' and r1['stato'] == 'in coda', (r0, r1)

    s0 = wait_for_status(NODE0, request_id)
    s1 = wait_for_status(NODE1, request_id)

    # callback delivery check: did the fake backend actually receive both?
    time.sleep(2)
    callbacks = http_json(f'{FAKE_BACKEND}/callback-test/{request_id}')
    callback_party_ids = sorted(c['party_id'] for c in callbacks)

    result = {
        'rep': rep,
        'request_id': request_id,
        'party0': s0,
        'party1': s1,
        'callback_party_ids_received': callback_party_ids,
        'callback_ok': callback_party_ids == [0, 1],
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f'run{rep}.json').write_text(json.dumps(result, indent=2))
    print(f'  party0: {s0.get("stato")} {s0.get("durata_secondi")}s   '
          f'party1: {s1.get("stato")} {s1.get("durata_secondi")}s   '
          f'callback_ok={result["callback_ok"]}', flush=True)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reps', type=int, default=5)
    args = ap.parse_args()

    results = []
    for rep in range(1, args.reps + 1):
        results.append(run_once(rep))

    print('\n=== riepilogo ===')
    for r in results:
        print(f"rep{r['rep']}: p0={r['party0'].get('durata_secondi')}s "
              f"p1={r['party1'].get('durata_secondi')}s "
              f"esito={r['party0'].get('esito')} callback_ok={r['callback_ok']}")


if __name__ == '__main__':
    main()
