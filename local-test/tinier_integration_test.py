#!/usr/bin/env python3
"""Tinier-integration verification battery, node/README.md's
tinier-integration section, Step 3. Same rigor and same methodology
already used for mascot (run_repeated_success.py, run_repeated_strangers.py,
the manual N=3 tests behind node/README.md sec:9.2-9.6), extended to a
protocol PARAMETER per request instead of assuming mascot.

Deliberately an ISOLATED topology (tint-node{0,1,2}-*, ports 1820x),
never the real production node0-net/node1-net/node2-net or the live
backend -- the whole point of tests 2/3/6 is to kill workers and hammer
the queue with back-to-back requests, which must never touch traffic
real site visitors depend on. Same real-network mechanism deploy_nodes.sh
uses for the live deployment (independent Docker networks, host's own
routable IP as PARTY0_HOST, published MP-SPDZ port ranges) -- not a
shortcut, just isolated.

port_base allocation: a simple monotonically-increasing counter (never
reused within one test session), not the orchestrator's full ring
buffer with min-reuse-age -- this harness submits requests itself, in
a single session, so "never reused at all" is a strictly stronger
guarantee than the production ring buffer needs to provide and needs no
separate justification.

CLI: python3 local-test/tinier_integration_test.py <test1|test2|test3|test5|test6> [options]
"""
import argparse
import json
import random
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
RESULTS_DIR = HERE / 'results_tinier_integration'
IMAGE = 'collateral-node:local'

WALLET = '0xclean0000000000000000000000000000000001'
NEW_LOAN = 50000
COLLATERAL_ASSET = 'BTC'
COLLATERAL_AMOUNT = 3.5
TOTAL_EXISTING_DEBT = 20000

HTTP_PORTS_N3 = [18200, 18201, 18202]
HTTP_PORTS_N2 = [18210, 18211]

PORT_POOL_BASE = 30000
PORT_POOL_STRIDE = 20  # > N_PLATFORMS(max 3), disjoint per-party ranges


def run(cmd, check=True, capture=False, timeout=None):
    print(f'  $ {cmd}', flush=True)
    return subprocess.run(cmd, shell=True, check=check,
                           capture_output=capture, text=True, timeout=timeout)


def host_ip():
    out = subprocess.run(['hostname', '-I'], capture_output=True, text=True, check=True)
    return out.stdout.split()[0]


class Topology:
    """N-node isolated topology, persists across many requests (no
    teardown between them) -- needed for tests 2/3/6, which are
    specifically about back-to-back requests on the SAME running
    containers, not a freshly restarted stack each time (that's test1's
    job, see run_once_isolated below)."""

    def __init__(self, n, name_prefix='tint'):
        self.n = n
        self.prefix = name_prefix
        self.http_ports = (HTTP_PORTS_N3 if n == 3 else HTTP_PORTS_N2)[:n]
        self.party0_host = host_ip()
        self._port_counter = 0

    def containers(self):
        cs = []
        for i in range(self.n):
            cs += [f'{self.prefix}-node{i}-redis', f'{self.prefix}-node{i}-api',
                   f'{self.prefix}-node{i}-worker']
        return cs

    def networks(self):
        return [f'{self.prefix}-node{i}-net' for i in range(self.n)]

    def teardown(self):
        for c in self.containers():
            run(f'docker rm -f {c}', check=False, capture=True)
        for net in self.networks():
            run(f'docker network rm {net}', check=False, capture=True)

    def _port_flags(self, party_id):
        # Every pool slot's party_id-th port, published 1:1 -- same
        # convention as deploy/deploy_nodes.sh's build_port_flags, just
        # with this harness's own disjoint base/stride so it can never
        # collide with the live deployment's real port pool.
        flags = []
        for slot in range(60):  # generous headroom for up to 60 requests/session
            port = PORT_POOL_BASE + slot * PORT_POOL_STRIDE + party_id
            flags.append(f'-p {port}:{port}')
        return ' '.join(flags)

    def setup(self):
        self.teardown()
        for net in self.networks():
            run(f'docker network create {net}')
        for i in range(self.n):
            net = f'{self.prefix}-node{i}-net'
            http_port = self.http_ports[i]
            run(f'docker run -d --name {self.prefix}-node{i}-redis --network {net} '
                f'redis:7-alpine redis-server --save "" --appendonly no')
            run(f'docker run -d --name {self.prefix}-node{i}-api --network {net} '
                f'-p {http_port}:8000 '
                f'-e PARTY_ID={i} -e N_PLATFORMS={self.n} -e PARTY0_HOST={self.party0_host} '
                f'-e HTTP_PORT=8000 -e REDIS_URL=redis://{self.prefix}-node{i}-redis:6379/0 '
                f'{IMAGE} api')
            run(f'docker run -d --name {self.prefix}-node{i}-worker --network {net} '
                f'{self._port_flags(i)} '
                f'-e PARTY_ID={i} -e N_PLATFORMS={self.n} -e PARTY0_HOST={self.party0_host} '
                f'-e REDIS_URL=redis://{self.prefix}-node{i}-redis:6379/0 '
                f'{IMAGE} worker')
        time.sleep(3)
        self._exchange_certs()
        for i in range(self.n):
            self._wait_health(f'http://localhost:{self.http_ports[i]}')

    def _exchange_certs(self):
        pems = {}
        for i in range(self.n):
            r = run(f'docker exec {self.prefix}-node{i}-worker cat '
                    f'/usr/src/MP-SPDZ/Player-Data/P{i}.pem', capture=True)
            pems[i] = r.stdout
        for i in range(self.n):
            for j in range(self.n):
                if i == j:
                    continue
                tmp = Path(f'/tmp/{self.prefix}_p{j}.pem')
                tmp.write_text(pems[j])
                run(f'docker cp {tmp} {self.prefix}-node{i}-worker:'
                    f'/usr/src/MP-SPDZ/Player-Data/P{j}.pem')
            run(f'docker exec {self.prefix}-node{i}-worker bash -c '
                f'"c_rehash Player-Data || openssl rehash Player-Data"')

    def _wait_health(self, base, timeout=180):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                d = http_json(f'{base}/salute')
                if d.get('ok'):
                    return
            except Exception:
                pass
            time.sleep(2)
        raise RuntimeError(f'{base}/salute never healthy within {timeout}s')

    def next_port_base(self):
        p = PORT_POOL_BASE + self._port_counter * PORT_POOL_STRIDE
        self._port_counter += 1
        return p

    def node_url(self, i):
        return f'http://localhost:{self.http_ports[i]}'

    def submit(self, request_id, protocol, debts, callback='http://192.0.2.1:9/cb'):
        """debts: list of length n, debts[0] is party0's own (requester)."""
        port_base = self.next_port_base()
        results = []
        for i in range(self.n):
            body = {'request_id': request_id, 'callback_url': callback,
                    'existing_debt_usd': debts[i], 'port_base': port_base,
                    'protocol': protocol}
            if i == 0:
                body.update(wallet_address=WALLET, new_loan_usd=NEW_LOAN,
                             collateral_asset=COLLATERAL_ASSET,
                             collateral_amount=COLLATERAL_AMOUNT)
            r = http_json(f'{self.node_url(i)}/calcola', body)
            results.append(r)
        return results, port_base

    def wait_all_status(self, request_id, timeout=600):
        t0 = time.time()
        states = [None] * self.n
        while time.time() - t0 < timeout:
            for i in range(self.n):
                if states[i] is not None and states[i].get('stato') in ('completato', 'fallito'):
                    continue
                try:
                    d = http_json(f'{self.node_url(i)}/stato/{request_id}')
                    states[i] = d
                except Exception:
                    pass
            if all(s is not None and s.get('stato') in ('completato', 'fallito') for s in states):
                return states, time.time() - t0
            time.sleep(1)
        return states, time.time() - t0


def http_json(url, data=None, timeout=15):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method='POST' if data else 'GET',
                                  headers={'Content-Type': 'application/json'} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def even_split(total, n):
    base, remainder = divmod(total, n)
    return [base + (1 if i < remainder else 0) for i in range(n)]


def save(name, obj):
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / name).write_text(json.dumps(obj, indent=2, default=str))
    print(f'  saved {RESULTS_DIR / name}')


# --------------------------------------------------------------------
# Test 1: success path repeated 5x, N=2, teardown+rebuild between reps
# (mirrors run_repeated_strangers.py's own methodology exactly, the
# source of mascot's 6.4-6.5%/10.6-10.7% CV baseline) -- tinier only.
# --------------------------------------------------------------------
def test1(reps=5, protocol='tinier'):
    results = []
    for rep in range(1, reps + 1):
        print(f'\n##### test1 rep {rep}: full teardown + rebuild, N=2, protocol={protocol} #####')
        topo = Topology(n=2, name_prefix='tint2')
        topo.setup()
        debts = even_split(TOTAL_EXISTING_DEBT, 2)
        request_id = f'test1-{rep}-{int(time.time())}'
        t0 = time.time()
        topo.submit(request_id, protocol, debts)
        states, waited = topo.wait_all_status(request_id)
        topo.teardown()
        entry = {'rep': rep, 'request_id': request_id, 'states': states, 'waited_s': waited}
        results.append(entry)
        print(f'  -> {[s.get("stato") for s in states]} '
              f'durate={[s.get("durata_secondi") for s in states]}')
    save(f'test1_success_repeated_{protocol}.json', results)
    times = [s['durata_secondi'] for r in results for s in r['states']
             if s.get('durata_secondi') is not None]
    if times:
        import statistics as st
        mean, sd = st.mean(times), (st.stdev(times) if len(times) > 1 else 0)
        cv = 100 * sd / mean if mean else 0
        print(f'\n=== test1 summary: n={len(times)} mean={mean:.2f}s '
              f'min={min(times):.2f}s max={max(times):.2f}s CV={cv:.1f}% ===')
        print('mascot baseline (node/README.md sec:8.1/8.2): CV 6.4-10.7%')
    return results


# --------------------------------------------------------------------
# Test 2: party killed mid-computation, N=3, protocol=tinier
# --------------------------------------------------------------------
def test2(protocol='tinier', kill_party=2):
    topo = Topology(n=3, name_prefix='tint3')
    topo.setup()
    debts = even_split(TOTAL_EXISTING_DEBT, 3)
    request_id = f'test2-kill-{int(time.time())}'
    print(f'\n##### test2: kill party {kill_party} mid-computation, protocol={protocol} #####')
    t_submit = time.time()
    topo.submit(request_id, protocol, debts)

    binary = 'tinier-party.x' if protocol == 'tinier' else 'mascot-party.x'
    container = f'tint3-node{kill_party}-worker'
    # Poll for the party binary to actually be running before killing --
    # same discipline node/README.md's own kill tests used ("confermato
    # mascot-party.x già attivo prima del kill"), not a blind fixed sleep.
    confirmed_running = False
    t_poll0 = time.time()
    while time.time() - t_poll0 < 30:
        r = run(f'docker exec {container} pgrep -f {binary}', check=False, capture=True)
        if r.returncode == 0 and r.stdout.strip():
            confirmed_running = True
            break
        time.sleep(0.05)
    t_confirmed_running = time.time()
    # Land mid-run, not at the very first instant it appears -- tinier's
    # whole run is short (~5-15s at N=3, see results_tinier_vs_mascot/),
    # so the margin here is deliberately small.
    time.sleep(1.5)
    t_kill = time.time()
    run(f'docker kill {container}')
    print(f'  killed at t+{t_kill - t_submit:.2f}s '
          f'(process confirmed running at t+{t_confirmed_running - t_submit:.2f}s)')

    survivors = [i for i in range(3) if i != kill_party]
    states = {}
    t_detect0 = time.time()
    detection_times = {}
    deadline = time.time() + 60
    while time.time() < deadline and len(detection_times) < len(survivors):
        for i in survivors:
            if i in detection_times:
                continue
            try:
                d = http_json(f'{topo.node_url(i)}/stato/{request_id}')
                if d.get('stato') in ('completato', 'fallito'):
                    detection_times[i] = time.time() - t_kill
                    states[i] = d
            except Exception:
                pass
        time.sleep(0.1)
    # killed party's own state, once its container/api is reachable again
    try:
        killed_state = http_json(f'{topo.node_url(kill_party)}/stato/{request_id}')
    except Exception as e:
        killed_state = {'error': str(e)}

    result = {
        'protocol': protocol, 'kill_party': kill_party, 'request_id': request_id,
        'confirmed_running_before_kill': confirmed_running,
        'kill_offset_from_submit_s': round(t_kill - t_submit, 2),
        'detection_times_s': {str(k): round(v, 2) for k, v in detection_times.items()},
        'survivor_states': states,
        'killed_party_state': killed_state,
    }
    topo.teardown()
    save(f'test2_kill_mid_computation_{protocol}.json', result)
    print(f'\n=== test2 summary: detection times {result["detection_times_s"]} ===')
    print('mascot baseline (node/README.md sec:8.5/9.3): <1s detection on all survivors')
    return result


# --------------------------------------------------------------------
# Test 3: 25 consecutive no-pause requests, N=3, protocol=tinier
# (equivalent to sec:9.5/9.6's mascot baseline: 3/25, 12%)
# --------------------------------------------------------------------
def test3(n_requests=25, protocol='tinier'):
    topo = Topology(n=3, name_prefix='tint3')
    topo.setup()
    results = []
    print(f'\n##### test3: {n_requests} consecutive no-pause requests, protocol={protocol} #####')
    for i in range(1, n_requests + 1):
        debts = [d + i for d in even_split(TOTAL_EXISTING_DEBT, 3)]  # slightly different each time
        request_id = f'test3-{i}-{int(time.time() * 1000)}'
        print(f'\n--- [{i}/{n_requests}] {request_id} ---', flush=True)
        t0 = time.time()
        try:
            topo.submit(request_id, protocol, debts)
        except Exception as e:
            entry = {'i': i, 'request_id': request_id, 'submit_error': str(e)}
            results.append(entry)
            save('test3_stress_25.json', results)
            continue
        states, waited = topo.wait_all_status(request_id, timeout=600)
        elapsed = time.time() - t0
        expected_exposure = sum(debts) + NEW_LOAN
        actual_exposures = [s.get('esposizione_totale_usd') for s in states]
        mismatch = any(a is not None and a != expected_exposure for a in actual_exposures)
        entry = {
            'i': i, 'request_id': request_id, 'elapsed_s': round(elapsed, 2),
            'debts': debts, 'expected_exposure_usd': expected_exposure,
            'actual_exposures_usd': actual_exposures,
            'stati': [s.get('stato') for s in states],
            'mismatch_esposizione': mismatch,
            'errori': [s.get('errore') for s in states],
        }
        results.append(entry)
        print(f'  -> stati={entry["stati"]} elapsed={elapsed:.1f}s mismatch={mismatch}')
        save('test3_stress_25.json', results)
    topo.teardown()

    successi = sum(1 for r in results if r.get('stati') == ['completato'] * 3)
    mismatches = sum(1 for r in results if r.get('mismatch_esposizione'))
    print(f'\n=== test3 summary: {successi}/{len(results)} successi, '
          f'{mismatches} mismatch esposizione ===')
    print('mascot baseline (node/README.md sec:9.6): 3/25 (12%), 0 mismatch, before AND after the port fix')
    return results


# --------------------------------------------------------------------
# Test 5: coordinator network address (PARTY0_HOST) behaviour for tinier
# --------------------------------------------------------------------
def test5(protocol='tinier'):
    print(f'\n##### test5: PARTY0_HOST=localhost (expected to hang/fail), protocol={protocol} #####')
    topo = Topology(n=2, name_prefix='tint5')
    topo.teardown()
    for net in topo.networks():
        run(f'docker network create {net}')
    # Deliberately broken: PARTY0_HOST=localhost, same bug scenario as
    # node/app/config.py's own docstring for mascot -- verifying whether
    # tinier's network coordination code path fails the SAME way (a
    # silent hang after initial handshake) or differently.
    for i in range(2):
        net = f'tint5-node{i}-net'
        http_port = 18220 + i
        run(f'docker run -d --name tint5-node{i}-redis --network {net} '
            f'redis:7-alpine redis-server --save "" --appendonly no')
        run(f'docker run -d --name tint5-node{i}-api --network {net} '
            f'-p {http_port}:8000 '
            f'-e PARTY_ID={i} -e N_PLATFORMS=2 -e PARTY0_HOST=localhost '
            f'-e HTTP_PORT=8000 -e REDIS_URL=redis://tint5-node{i}-redis:6379/0 '
            f'{IMAGE} api')
        run(f'docker run -d --name tint5-node{i}-worker --network {net} '
            f'-p {30000 + i}:{30000 + i} '
            f'-e PARTY_ID={i} -e N_PLATFORMS=2 -e PARTY0_HOST=localhost '
            f'-e REDIS_URL=redis://tint5-node{i}-redis:6379/0 '
            f'{IMAGE} worker')
    time.sleep(3)
    pems = {}
    for i in range(2):
        r = run(f'docker exec tint5-node{i}-worker cat /usr/src/MP-SPDZ/Player-Data/P{i}.pem',
                capture=True)
        pems[i] = r.stdout
    for i in range(2):
        j = 1 - i
        tmp = Path(f'/tmp/tint5_p{j}.pem')
        tmp.write_text(pems[j])
        run(f'docker cp {tmp} tint5-node{i}-worker:/usr/src/MP-SPDZ/Player-Data/P{j}.pem')
        run(f'docker exec tint5-node{i}-worker bash -c "c_rehash Player-Data || openssl rehash Player-Data"')

    node0, node1 = 'http://localhost:18220', 'http://localhost:18221'
    for base in (node0, node1):
        t0 = time.time()
        while time.time() - t0 < 60:
            try:
                if http_json(f'{base}/salute').get('ok'):
                    break
            except Exception:
                pass
            time.sleep(2)

    debts = even_split(TOTAL_EXISTING_DEBT, 2)
    request_id = f'test5-localhost-{int(time.time())}'
    body0 = {'request_id': request_id, 'callback_url': 'http://192.0.2.1:9/cb',
             'existing_debt_usd': debts[0], 'port_base': 30000, 'protocol': protocol,
             'wallet_address': WALLET, 'new_loan_usd': NEW_LOAN,
             'collateral_asset': COLLATERAL_ASSET, 'collateral_amount': COLLATERAL_AMOUNT}
    body1 = {'request_id': request_id, 'callback_url': 'http://192.0.2.1:9/cb',
             'existing_debt_usd': debts[1], 'port_base': 30000, 'protocol': protocol}
    http_json(f'{node0}/calcola', body0)
    http_json(f'{node1}/calcola', body1)

    t0 = time.time()
    states = [None, None]
    while time.time() - t0 < 90:
        for i, base in enumerate((node0, node1)):
            try:
                d = http_json(f'{base}/stato/{request_id}')
                states[i] = d
            except Exception:
                pass
        if all(s is not None and s.get('stato') in ('completato', 'fallito') for s in states):
            break
        time.sleep(2)
    waited = time.time() - t0

    result = {'protocol': protocol, 'party0_host': 'localhost', 'waited_s': round(waited, 1),
              'states': states,
              'hung_like_mascot': waited >= 89 and all(
                  s is None or s.get('stato') == 'in corso' for s in states)}
    for i in range(2):
        run(f'docker rm -f tint5-node{i}-redis tint5-node{i}-api tint5-node{i}-worker',
            check=False, capture=True)
    for net in (f'tint5-node0-net', f'tint5-node1-net'):
        run(f'docker network rm {net}', check=False, capture=True)
    save(f'test5_network_address_{protocol}.json', result)
    print(f'\n=== test5 summary: waited={waited:.1f}s states={states} ===')
    print('mascot baseline (node/app/config.py docstring): localhost -> silent hang, '
          'no error, forever (until MPC_TIMEOUT_SECONDS)')
    return result


# --------------------------------------------------------------------
# Test 6: 20+ consecutive requests, unpredictable mixed protocol order
# --------------------------------------------------------------------
def test6(n_requests=20, seed=20260818):
    topo = Topology(n=3, name_prefix='tint3')
    topo.setup()
    rng = random.Random(seed)
    protocols = [rng.choice(['mascot', 'tinier']) for _ in range(n_requests)]
    # guarantee it's not a regular alternating pattern (explicit
    # instruction: avoid "one yes one no" which could hide accidental
    # queue synchronization) -- re-roll if it comes out perfectly
    # alternating.
    if protocols == [protocols[0], protocols[1]] * (n_requests // 2):
        protocols[3], protocols[4] = protocols[4], protocols[3]
    print(f'\n##### test6: {n_requests} consecutive no-pause requests, mixed protocol #####')
    print(f'  sequence: {protocols}')
    results = []
    for i, protocol in enumerate(protocols, start=1):
        debts = [d + i for d in even_split(TOTAL_EXISTING_DEBT, 3)]
        request_id = f'test6-{i}-{protocol}-{int(time.time() * 1000)}'
        print(f'\n--- [{i}/{n_requests}] {request_id} protocol={protocol} ---', flush=True)
        t0 = time.time()
        try:
            topo.submit(request_id, protocol, debts)
        except Exception as e:
            entry = {'i': i, 'protocol': protocol, 'request_id': request_id, 'submit_error': str(e)}
            results.append(entry)
            save('test6_mixed_protocol_20.json', results)
            continue
        states, waited = topo.wait_all_status(request_id, timeout=600)
        elapsed = time.time() - t0
        expected_exposure = sum(debts) + NEW_LOAN
        actual_exposures = [s.get('esposizione_totale_usd') for s in states]
        # tinier never reveals exposure (see mpc_runner.parse_result) --
        # only check the mismatch condition for mascot runs.
        mismatch = (protocol == 'mascot' and
                    any(a is not None and a != expected_exposure for a in actual_exposures))
        entry = {
            'i': i, 'protocol': protocol, 'request_id': request_id,
            'elapsed_s': round(elapsed, 2), 'debts': debts,
            'expected_exposure_usd': expected_exposure if protocol == 'mascot' else None,
            'actual_exposures_usd': actual_exposures,
            'stati': [s.get('stato') for s in states],
            'mismatch_esposizione': mismatch,
            'errori': [s.get('errore') for s in states],
        }
        results.append(entry)
        print(f'  -> stati={entry["stati"]} elapsed={elapsed:.1f}s mismatch={mismatch}')
        save('test6_mixed_protocol_20.json', results)
    topo.teardown()

    successi = sum(1 for r in results if r.get('stati') == ['completato'] * 3)
    mismatches = sum(1 for r in results if r.get('mismatch_esposizione'))
    print(f'\n=== test6 summary: {successi}/{len(results)} successi, '
          f'{mismatches} mismatch esposizione (mascot-only check) ===')
    print('mascot-only baseline for comparison (sec:9.6): 3/25 (12%), 0 mismatch')
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['test1', 'test2', 'test3', 'test5', 'test6'])
    ap.add_argument('--reps', type=int, default=None)
    ap.add_argument('--n-requests', type=int, default=None)
    ap.add_argument('--protocol', default='tinier')
    args = ap.parse_args()

    if args.cmd == 'test1':
        test1(reps=args.reps or 5, protocol=args.protocol)
    elif args.cmd == 'test2':
        test2(protocol=args.protocol)
    elif args.cmd == 'test3':
        test3(n_requests=args.n_requests or 25, protocol=args.protocol)
    elif args.cmd == 'test5':
        test5(protocol=args.protocol)
    elif args.cmd == 'test6':
        test6(n_requests=args.n_requests or 20)


if __name__ == '__main__':
    main()
