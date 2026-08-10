#!/usr/bin/env python3
"""
Full-catalogue run: every protocol valid at N=2 and N=5, same scenario as
Scripts/run_scaling_study.py (same wallet, loan, collateral, total
aggregate debt -- only the split and the protocol/domain change). One
output file per (protocol, N) combination, as proof backing the
published benchmark report's charts.

--mode benchmark (default) writes results_all_algorithms/ -- the
cross-domain correctness sweep (includes binary-domain protocols:
yao/ccd/mal-ccd/semi-bin/tinier/tiny), already run and NOT meant to be
re-run casually (it's the evidence that the risk computation gives the
same answer regardless of protocol).

--mode production writes results_produzione/ -- the real-deployment
sweep. mode=2 needs secret-arithmetic composition to hide the 5
sub-scores/7 flags from all but the final result, which mpc_bench's own
compile_domain() refuses for the binary domain (see its ValueError/
sys.exit message) -- binary-domain protocols are still ATTEMPTED here
(not skipped), so that refusal is captured as an explicit, fast,
documented exclusion rather than silently omitted from the sweep.

A protocol is "valid" at N if it scales to arbitrary N (per
mpc_bench.PROTOCOLS), or is hard-wired to exactly that N (only yao, at
N=2 -- no protocol in the catalogue is hard-wired to N=5, so the N=5 set
is scalable protocols only).

Live price (CoinGecko, $COINGECKO_API_KEY), fixture wallet history -- see
crypto_risk_run.py's module docstring for why.

Usage: python3 Scripts/run_all_algorithms.py [--mode benchmark|production] [--resume]
  --resume: skip (protocol, N) pairs that already have a status=ok file
Writes results_all_algorithms|results_produzione/n<N>/<protocol>.json
(+ .log, .meta.json) and .../summary.json.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
OUT_ROOT_BY_MODE = {
    'benchmark': REPO_ROOT / 'results_all_algorithms',
    'production': REPO_ROOT / 'results_produzione',
}

sys.path.insert(0, str(Path(__file__).parent))
import mpc_bench  # noqa: E402

WALLET = '0xclean0000000000000000000000000000000001'
NEW_LOAN = 50000
COLLATERAL_ASSET = 'BTC'
COLLATERAL_AMOUNT = 3.5
TOTAL_EXISTING_DEBT = 20000

N_VALUES = [2, 5]

# Timeout tuning: OT/HE malicious+covert protocols are the ones that
# blew past a minute even at N=2 (lowgear 17s, cowgear 17s, chaigear
# 45s, highgear timed out at 400s) and scaled ~200s+ at N=5 for mascot
# (the only one measured so far) -- give this family much more room,
# especially at N=5, where an even heavier protocol could need more time
# before either finishing or hitting a resource ceiling worth documenting.
# plain 'malicious' (no '(OT)'/'(HE)' suffix) covers spdz2k -- empirically
# just as slow as the suffixed ones (needed >400s at N=5) despite the
# catalogue's shorter label, found the hard way on a first pass with the
# narrower marker list below.
HEAVY_SECURITY_MARKERS = ('malicious (OT)', 'malicious (HE)', 'covert (HE)', 'malicious')
TIMEOUT_BY_N = {2: {'heavy': 600, 'default': 150}, 5: {'heavy': 1200, 'default': 400}}


def even_split(total, n):
    base, remainder = divmod(total, n)
    return [base + (1 if i < remainder else 0) for i in range(n)]


def valid_protocols(n):
    out = []
    for p in mpc_bench.PROTOCOLS:
        name, binary, domain, majority, security, scales, fixed_n = p
        if scales or fixed_n == n:
            out.append(p)
    return out


def run_one(protocol, domain, security, n, resume, mode, out_root):
    out_dir = out_root / f'n{n}'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{protocol}.json'
    log_path = out_dir / f'{protocol}.log'
    raw_path = out_dir / f'{protocol}.mpc_raw.log'
    meta_path = out_dir / f'{protocol}.meta.json'

    if resume and meta_path.exists():
        try:
            prev = json.loads(meta_path.read_text())
            if prev.get('status') == 'ok':
                print(f'  [skip, already ok] {protocol} N={n}')
                return 'ok'
        except (json.JSONDecodeError, OSError):
            pass

    debts = even_split(TOTAL_EXISTING_DEBT, n)
    debts_str = ','.join(str(d) for d in debts)
    is_heavy = any(m in security for m in HEAVY_SECURITY_MARKERS)
    timeout = TIMEOUT_BY_N[n]['heavy' if is_heavy else 'default']

    cmd = [
        sys.executable, 'Scripts/crypto_risk_run.py',
        '--wallet', WALLET,
        '--platform-debts', debts_str,
        '--new-loan', str(NEW_LOAN),
        '--collateral-asset', COLLATERAL_ASSET,
        '--collateral-amount', str(COLLATERAL_AMOUNT),
        '--domain', domain, '--protocol', protocol, '--mode', mode,
        '--timeout', str(timeout),
        '--out', str(out_path),
        '--raw-log', str(raw_path),
    ]
    api_key = os.environ.get('COINGECKO_API_KEY')
    if api_key:
        cmd += ['--coingecko-api-key', api_key]

    print(f'  === {protocol} (domain={domain}, N={n}, timeout={timeout}s) ===', flush=True)
    t0 = datetime.datetime.now()
    timestamp = t0.replace(tzinfo=datetime.timezone.utc).isoformat()
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

    meta = {
        'timestamp': timestamp, 'protocol': protocol, 'domain': domain,
        'security': security, 'n_platforms': n, 'mode': mode,
        'command': ' '.join(a if a != api_key else '***' for a in cmd) if api_key else ' '.join(cmd),
        'status': status, 'wall_clock_seconds': round(wall, 2),
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f'      -> {status}, {wall:.1f}s', flush=True)
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=sorted(OUT_ROOT_BY_MODE), default='benchmark')
    ap.add_argument('--resume', action='store_true')
    args = ap.parse_args()
    out_root = OUT_ROOT_BY_MODE[args.mode]

    out_root.mkdir(exist_ok=True)
    summary = []
    for n in N_VALUES:
        print(f'\n##### N={n} (mode={args.mode}) #####')
        for name, binary, domain, majority, security, scales, fixed_n in valid_protocols(n):
            status = run_one(name, domain, security, n, args.resume, args.mode, out_root)
            summary.append({'protocol': name, 'domain': domain, 'majority': majority,
                             'security': security, 'n_platforms': n, 'status': status})
            (out_root / 'summary.json').write_text(json.dumps(summary, indent=2))

    print('\n=== riepilogo ===')
    for row in summary:
        print(f"N={row['n_platforms']:>2}  {row['protocol']:<15} {row['status']}")


if __name__ == '__main__':
    main()
