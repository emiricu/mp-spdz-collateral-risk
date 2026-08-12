#!/usr/bin/env python3
"""Same success-path scenario as run_repeated_success.py, but WITHOUT the
shortcut a real two-machine deployment would never have: no shared
docker-compose network, no Compose-internal DNS. The two nodes sit on
two Docker networks created independently and never connected to each
other -- the only path between them is through the host's own real
network interface, via published ports, exactly the same class of path
they'll use once they're on two separate physical machines.

PARTY0_HOST is set to the host's actual routable interface IP (see
--host-ip), never a container/service name and never localhost.

Port layout, deliberately asymmetric (see README's "strangers on one
host" section for why symmetric publishing isn't needed): node 0's own
MP-SPDZ port is published 1:1 to the host (it's the only one ever dialed
into -- party 1 always connects OUT to party 0, never the reverse, for
N=2). Node 1's MP-SPDZ port stays private to its own isolated network.

Usage: python3 local-test/run_repeated_strangers.py --host-ip <ip> --reps 5
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
IMAGE = 'collateral-node:local'

WALLET = '0xclean0000000000000000000000000000000001'
SCENARIO_P0 = {
    'wallet_address': WALLET, 'existing_debt_usd': 10000, 'new_loan_usd': 50000,
    'collateral_asset': 'BTC', 'collateral_amount': 3.5,
}
SCENARIO_P1 = {'existing_debt_usd': 10000}

NET0 = 'strangers-node0-net'
NET1 = 'strangers-node1-net'
PORT_BASE = 5000
HTTP0_HOST_PORT = 18100
HTTP1_HOST_PORT = 18101
# MUST equal PORT_BASE: mascot-party.x's -pn value is used BOTH as "my
# own internal port" AND, by every other party, as the literal number to
# dial on PARTY0_HOST -- a real bug in an earlier version of this script
# published node 0's port to a DIFFERENT host port (15000) while parties
# still dialed the raw PORT_BASE (5000), which the host wasn't actually
# listening on -- connection refused, silently retried into what looked
# like a 600s hang. Verified directly: 1:1 mapping is required, not
# optional dressing.
MPSPDZ0_HOST_PORT = PORT_BASE

CONTAINERS = [
    'strangers-node0-redis', 'strangers-node0-api', 'strangers-node0-worker',
    'strangers-node1-redis', 'strangers-node1-api', 'strangers-node1-worker',
]


def run(cmd, check=True, capture=False):
    print(f'  $ {cmd}', flush=True)
    return subprocess.run(cmd, shell=True, check=check,
                           capture_output=capture, text=True)


def teardown():
    for c in CONTAINERS:
        run(f'docker rm -f {c}', check=False, capture=True)
    for n in (NET0, NET1):
        run(f'docker network rm {n}', check=False, capture=True)


def setup(party0_host):
    run(f'docker network create {NET0}')
    run(f'docker network create {NET1}')

    # --- node 0 ---
    run(f'docker run -d --name strangers-node0-redis --network {NET0} '
        f'redis:7-alpine redis-server --save "" --appendonly no')
    run(f'docker run -d --name strangers-node0-api --network {NET0} '
        f'-p {HTTP0_HOST_PORT}:8000 '
        f'-e PARTY_ID=0 -e PARTY0_HOST={party0_host} -e PORT_BASE={PORT_BASE} '
        f'-e HTTP_PORT=8000 -e REDIS_URL=redis://strangers-node0-redis:6379/0 '
        f'{IMAGE} api')
    run(f'docker run -d --name strangers-node0-worker --network {NET0} '
        f'-p {MPSPDZ0_HOST_PORT}:{PORT_BASE} '
        f'-e PARTY_ID=0 -e PARTY0_HOST={party0_host} -e PORT_BASE={PORT_BASE} '
        f'-e REDIS_URL=redis://strangers-node0-redis:6379/0 '
        f'{IMAGE} worker')

    # --- node 1: no MP-SPDZ port published, only reachable via its own outbound dial ---
    run(f'docker run -d --name strangers-node1-redis --network {NET1} '
        f'redis:7-alpine redis-server --save "" --appendonly no')
    run(f'docker run -d --name strangers-node1-api --network {NET1} '
        f'-p {HTTP1_HOST_PORT}:8000 '
        f'-e PARTY_ID=1 -e PARTY0_HOST={party0_host} -e PORT_BASE={PORT_BASE} '
        f'-e HTTP_PORT=8000 -e REDIS_URL=redis://strangers-node1-redis:6379/0 '
        f'{IMAGE} api')
    run(f'docker run -d --name strangers-node1-worker --network {NET1} '
        f'-e PARTY_ID=1 -e PARTY0_HOST={party0_host} -e PORT_BASE={PORT_BASE} '
        f'-e REDIS_URL=redis://strangers-node1-redis:6379/0 '
        f'{IMAGE} worker')

    # certs: generate independently in each container (no cross-copy helper here --
    # this script fetches each .pem out and pushes it into the other's volume,
    # the same manual step node/README.md documents for the real two-machine case.
    time.sleep(3)
    run('docker exec strangers-node0-worker cat /usr/src/MP-SPDZ/Player-Data/P0.pem',
        capture=True)  # trigger generation via entrypoint already done at container start


def exchange_certs():
    p0 = run('docker exec strangers-node0-worker cat /usr/src/MP-SPDZ/Player-Data/P0.pem',
              capture=True).stdout
    p1 = run('docker exec strangers-node1-worker cat /usr/src/MP-SPDZ/Player-Data/P1.pem',
              capture=True).stdout
    Path('/tmp/strangers_p0.pem').write_text(p0)
    Path('/tmp/strangers_p1.pem').write_text(p1)
    run('docker cp /tmp/strangers_p1.pem strangers-node0-worker:/usr/src/MP-SPDZ/Player-Data/P1.pem')
    run('docker cp /tmp/strangers_p0.pem strangers-node1-worker:/usr/src/MP-SPDZ/Player-Data/P0.pem')
    run('docker exec strangers-node0-worker bash -c "c_rehash Player-Data || openssl rehash Player-Data"')
    run('docker exec strangers-node1-worker bash -c "c_rehash Player-Data || openssl rehash Player-Data"')


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


def wait_for_status(url, request_id, timeout=700):
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


def run_once(rep, party0_host):
    print(f'\n##### strangers rep {rep}: full teardown + rebuild #####', flush=True)
    teardown()
    setup(party0_host)
    time.sleep(2)
    exchange_certs()

    node0 = f'http://localhost:{HTTP0_HOST_PORT}'
    node1 = f'http://localhost:{HTTP1_HOST_PORT}'
    print('  waiting for both nodes /salute...', flush=True)
    wait_for_health(node0)
    wait_for_health(node1)
    salute0 = http_json(f'{node0}/salute')
    salute1 = http_json(f'{node1}/salute')

    request_id = f'strangers-{rep}-{int(time.time())}'
    print(f'  submitting {request_id}', flush=True)
    # no fake backend reachable from either isolated network here -- callback
    # delivery isn't the point of this test, only whether the MPC handshake
    # and computation complete over a real host-routed path. Point the
    # callback at a URL that will simply fail fast; GET /stato is what we read.
    cb = 'http://192.0.2.1:9/callback-test'  # TEST-NET-1, guaranteed unreachable, fails fast
    r0 = http_json(f'{node0}/calcola', {'request_id': request_id, 'callback_url': cb, **SCENARIO_P0})
    r1 = http_json(f'{node1}/calcola', {'request_id': request_id, 'callback_url': cb, **SCENARIO_P1})
    assert r0['stato'] == 'in coda' and r1['stato'] == 'in coda', (r0, r1)

    try:
        s0 = wait_for_status(node0, request_id)
        s1 = wait_for_status(node1, request_id)
    except Exception:
        print('  !!! polling failed -- dumping worker logs before teardown for diagnosis', flush=True)
        run('docker logs strangers-node0-worker', check=False)
        run('docker logs strangers-node1-worker', check=False)
        run(f'docker exec strangers-node0-worker sh -c '
            f'"cat /usr/src/MP-SPDZ/node_logs/{request_id}.log 2>/dev/null || true"', check=False)
        run(f'docker exec strangers-node1-worker sh -c '
            f'"cat /usr/src/MP-SPDZ/node_logs/{request_id}.log 2>/dev/null || true"', check=False)
        raise

    result = {
        'rep': rep, 'request_id': request_id, 'party0_host': party0_host,
        'salute0': salute0, 'salute1': salute1,
        'party0': s0, 'party1': s1,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f'strangers_run{rep}.json').write_text(json.dumps(result, indent=2))
    print(f'  party0: {s0.get("stato")} {s0.get("durata_secondi")}s   '
          f'party1: {s1.get("stato")} {s1.get("durata_secondi")}s', flush=True)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host-ip', required=True,
                     help="the host's real routable interface IP (never 127.0.0.1)")
    ap.add_argument('--reps', type=int, default=5)
    args = ap.parse_args()

    results = []
    try:
        for rep in range(1, args.reps + 1):
            results.append(run_once(rep, args.host_ip))
    finally:
        teardown()

    print('\n=== riepilogo (strangers-on-one-host) ===')
    for r in results:
        print(f"rep{r['rep']}: p0={r['party0'].get('durata_secondi')}s "
              f"({r['party0'].get('stato')})  p1={r['party1'].get('durata_secondi')}s "
              f"({r['party1'].get('stato')})")


if __name__ == '__main__':
    main()
