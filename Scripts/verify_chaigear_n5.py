#!/usr/bin/env python3
"""
One-off flakiness check for chaigear@N=5: it failed (OOM-suspected crash)
in the results_all_algorithms/ (benchmark) sweep but succeeded in the
heavier results_produzione/ (production) sweep -- the wrong direction for
a stable resource ceiling (a MORE expensive mode shouldn't rescue a case
that failed for lack of resources in a LIGHTER one). Runs chaigear@N=5
three times per mode, strictly sequential (never overlapping with
anything else, to rule out resource contention as a confound), without
touching the existing sweep results.

Usage: python3 Scripts/verify_chaigear_n5.py
Writes results_all_algorithms/n5/verify/chaigear_n5_verify_run{1,2,3}.json
(+.log/.meta.json) and the same under results_produzione/n5/verify/.
"""
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import mpc_bench  # noqa: E402

WALLET = '0xclean0000000000000000000000000000000001'
NEW_LOAN = 50000
COLLATERAL_ASSET = 'BTC'
COLLATERAL_AMOUNT = 3.5
DEBTS = '4000,4000,4000,4000,4000'  # even split of $20,000 across N=5, same as the sweeps
TIMEOUT = 1200

OUT_DIRS = {
    'benchmark': REPO_ROOT / 'results_all_algorithms' / 'n5' / 'verify',
    'production': REPO_ROOT / 'results_produzione' / 'n5' / 'verify',
}


def run_one(mode, run_idx):
    out_dir = OUT_DIRS[mode]
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f'chaigear_n5_verify_run{run_idx}'
    out_path = out_dir / f'{stem}.json'
    log_path = out_dir / f'{stem}.log'
    raw_path = out_dir / f'{stem}.mpc_raw.log'
    meta_path = out_dir / f'{stem}.meta.json'

    cmd = [
        sys.executable, 'Scripts/crypto_risk_run.py',
        '--wallet', WALLET, '--platform-debts', DEBTS,
        '--new-loan', str(NEW_LOAN), '--collateral-asset', COLLATERAL_ASSET,
        '--collateral-amount', str(COLLATERAL_AMOUNT),
        '--domain', 'field', '--protocol', 'chaigear', '--mode', mode,
        '--timeout', str(TIMEOUT), '--out', str(out_path), '--raw-log', str(raw_path),
    ]
    api_key = os.environ.get('COINGECKO_API_KEY')
    if api_key:
        cmd += ['--coingecko-api-key', api_key]

    print(f'=== chaigear N=5 mode={mode} run {run_idx}/3 ===', flush=True)
    t0 = datetime.datetime.now()
    timestamp = t0.replace(tzinfo=datetime.timezone.utc).isoformat()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=TIMEOUT + 60, cwd=REPO_ROOT)
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

    meta = {
        'timestamp': timestamp, 'protocol': 'chaigear', 'domain': 'field',
        'n_platforms': 5, 'mode': mode, 'run_idx': run_idx,
        'command': ' '.join(a if a != api_key else '***' for a in cmd) if api_key else ' '.join(cmd),
        'status': status, 'wall_clock_seconds': round(wall, 2),
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f'    -> {status}, {wall:.1f}s', flush=True)
    return status


def main():
    results = {}
    for mode in ('benchmark', 'production'):
        results[mode] = []
        for i in (1, 2, 3):
            results[mode].append(run_one(mode, i))
    print('\n=== riepilogo verifica flakiness chaigear@N=5 ===')
    for mode, statuses in results.items():
        print(f'{mode}: {statuses}')


if __name__ == '__main__':
    main()
