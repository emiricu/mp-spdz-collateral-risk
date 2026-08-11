#!/usr/bin/env python3
"""
Controlled re-test: mascot and mama only, all four N (2/3/4/5), both
modes (benchmark/production), 3 repetitions each -- 48 runs total.

Purpose: the original two sweeps (results_all_algorithms/ and
results_produzione/) were run as separate sessions, not a controlled
comparison -- background system load, cache state, thermal conditions
could differ between them (the same class of confound already suspected
and cleared for chaigear@N5, see Scripts/verify_chaigear_n5.py). A
benchmark-vs-production inversion showed up at N=3/N=4 for mascot/mama
(benchmark slower than production, the opposite of N=2/N=5) that could
be a real effect or could be exactly this kind of artifact.

Controls applied, on top of what run_all_algorithms.py does:
  - alternated order: for each N, benchmark then production back-to-back,
    for each protocol, before moving to the next N -- so the two
    conditions being compared are always adjacent in time, not averaged
    over sweeps run hours or days apart.
  - frozen price (--price-source fixture): eliminates CoinGecko price
    drift AND network-call latency variance as confounds. Opt-in only
    here -- see crypto_risk_run.py's module docstring; nothing else in
    the pipeline is affected by this flag existing.
  - 3 full passes over the alternating sequence (not 3 consecutive reps
    of the same combination back-to-back) -- spreads any monotonic drift
    (e.g. thermal buildup over the session) symmetrically across all 16
    combinations instead of concentrating it in whichever combination
    happens to run last.
  - single dedicated session, nothing else running concurrently (checked
    before starting, see the caller).

Output: results_retest_controllato/<protocol>_<n>_<mode>_run<rep>.json
(full crypto_risk_run.py output, same shape as the other sweeps) plus a
matching .log per run and a running index.json rebuilt after every run.
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
OUT_ROOT = REPO_ROOT / 'results_retest_controllato'

WALLET = '0xclean0000000000000000000000000000000001'
NEW_LOAN = 50000
COLLATERAL_ASSET = 'BTC'
COLLATERAL_AMOUNT = 3.5
TOTAL_EXISTING_DEBT = 20000
DOMAIN = 'field'  # both mascot and mama are field-domain

PROTOCOLS = ['mascot', 'mama']
N_VALUES = [2, 3, 4, 5]
MODES = ['benchmark', 'production']
REPS = 3

# same heavy-protocol timeouts as run_all_algorithms.py's TIMEOUT_BY_N
# (both mascot and mama are 'malicious (OT)', always the heavy branch)
TIMEOUT_HEAVY_BY_N = {2: 600, 3: 800, 4: 1000, 5: 1200}


def even_split(total, n):
    base, remainder = divmod(total, n)
    return [base + (1 if i < remainder else 0) for i in range(n)]


def run_one(protocol, n, mode, rep):
    OUT_ROOT.mkdir(exist_ok=True)
    tag = f'{protocol}_{n}_{mode}_run{rep}'
    out_path = OUT_ROOT / f'{tag}.json'
    log_path = OUT_ROOT / f'{tag}.log'
    raw_path = OUT_ROOT / f'{tag}.mpc_raw.log'

    debts = even_split(TOTAL_EXISTING_DEBT, n)
    debts_str = ','.join(str(d) for d in debts)
    timeout = TIMEOUT_HEAVY_BY_N[n]

    cmd = [
        sys.executable, 'Scripts/crypto_risk_run.py',
        '--wallet', WALLET,
        '--platform-debts', debts_str,
        '--new-loan', str(NEW_LOAN),
        '--collateral-asset', COLLATERAL_ASSET,
        '--collateral-amount', str(COLLATERAL_AMOUNT),
        '--domain', DOMAIN, '--protocol', protocol, '--mode', mode,
        '--price-source', 'fixture',
        '--timeout', str(timeout),
        '--out', str(out_path),
        '--raw-log', str(raw_path),
    ]

    print(f'  === {tag} (timeout={timeout}s) ===', flush=True)
    t0 = datetime.datetime.now()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=timeout + 60, cwd=REPO_ROOT)
        wall = (datetime.datetime.now() - t0).total_seconds()
        log_path.write_text(r.stdout + '\n--- stderr ---\n' + r.stderr)
        ok = r.returncode == 0 and out_path.exists()
        status = 'ok' if ok else f'failed (returncode={r.returncode})'
    except subprocess.TimeoutExpired as exc:
        wall = (datetime.datetime.now() - t0).total_seconds()
        stdout = exc.stdout.decode() if exc.stdout else ''
        stderr = exc.stderr.decode() if exc.stderr else ''
        log_path.write_text(stdout + '\n--- stderr ---\n' + stderr + '\n--- TIMEOUT ---')
        status = 'timeout'

    print(f'      -> {status}, {wall:.1f}s', flush=True)
    return {'tag': tag, 'protocol': protocol, 'n_platforms': n, 'mode': mode,
            'rep': rep, 'status': status, 'wall_clock_seconds': round(wall, 2)}


def main():
    index = []
    index_path = OUT_ROOT / 'index.json'
    total = REPS * len(N_VALUES) * len(PROTOCOLS) * len(MODES)
    done = 0
    for rep in range(1, REPS + 1):
        for n in N_VALUES:
            for protocol in PROTOCOLS:
                for mode in MODES:
                    done += 1
                    print(f'\n##### [{done}/{total}] rep={rep} N={n} #####', flush=True)
                    entry = run_one(protocol, n, mode, rep)
                    index.append(entry)
                    index_path.write_text(json.dumps(index, indent=2))

    print('\n=== riepilogo ===')
    for e in index:
        print(f"{e['tag']:<40} {e['status']:<20} {e['wall_clock_seconds']:.1f}s")
    print('TUTTO COMPLETATO')


if __name__ == '__main__':
    main()
