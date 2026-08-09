#!/usr/bin/env python3
"""
Regression test: liquidation_price_usd() must be IDENTICAL between
--mode benchmark and --mode production, for the same scenario, at N=2
and N=3 -- not just "production >= benchmark". Both modes now reveal
max_ltv_bps AND liq_threshold_bps (see crypto_collateral_risk.mpc's
module docstring on why withholding liq_threshold_bps in production
bought no privacy -- it derives only from already-public data, never
from the cross-platform debt this program actually protects), so both
paths run the exact same formula (policy.liquidation_price_usd, shared
by both) on the exact same inputs and must agree exactly.

This test exists because that equality broke once already: an earlier
revision withheld liq_threshold_bps in production, silently forcing its
conservative estimate to substitute the looser max_ltv_bps as threshold
-- no test caught it. If a future change deliberately withholds some
other field from production again, THIS TEST MUST FAIL rather than pass
or be quietly loosened to ">=" -- that forces a conscious decision about
what relationship is actually expected, instead of a silent regression.

Run directly: python3 Scripts/test_crypto_risk_run.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

SCENARIO = dict(
    wallet='0xborderline00000000000000000000000003',
    new_loan='5000', collateral_asset='SOL', collateral_amount='100',
)

# (n_platforms, protocol, platform_debts) -- protocol must be N-scalable
# and match the domain (field here); semi for N=2 (dishonest-majority,
# native 2-party), shamir for N=3 (honest-majority, needs >=3 parties).
CASES = [
    (2, 'semi', ['0', '0']),
    (3, 'shamir', ['0', '0', '0']),
]


def run(mode, protocol, debts):
    # --out avoids parsing JSON out of stdout, which also carries
    # compile/build diagnostic lines printed before the final result.
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tmp:
        out_path = tmp.name
    cmd = [
        sys.executable, 'Scripts/crypto_risk_run.py',
        '--wallet', SCENARIO['wallet'],
        '--platform-debts', ','.join(debts),
        '--new-loan', SCENARIO['new_loan'],
        '--collateral-asset', SCENARIO['collateral_asset'],
        '--collateral-amount', SCENARIO['collateral_amount'],
        '--domain', 'field', '--protocol', protocol, '--mode', mode,
        '--out', out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=REPO_ROOT)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise RuntimeError(f'crypto_risk_run.py failed (mode={mode!r})')
    result = json.loads(Path(out_path).read_text())
    Path(out_path).unlink()
    return result


def main():
    failures = []
    for n_platforms, protocol, debts in CASES:
        bench = run('benchmark', protocol, debts)
        prod = run('production', protocol, debts)

        bench_thr = int(bench['mpc_output']['liq_threshold_bps'])
        prod_thr = prod['liq_threshold_bps']
        bench_price = bench['liquidation_price_usd']
        prod_price = prod['liquidation_price_usd']

        print(f'N={n_platforms}: benchmark  liq_threshold_bps={bench_thr}  '
              f'liquidation_price_usd={bench_price}')
        print(f'N={n_platforms}: production liq_threshold_bps={prod_thr}  '
              f'liquidation_price_usd={prod_price}')

        if prod_thr != bench_thr:
            failures.append(
                f'N={n_platforms}: liq_threshold_bps differs between modes '
                f'(benchmark={bench_thr}, production={prod_thr}) -- production '
                f'is withholding a field again; decide explicitly whether the '
                f'expected relationship is now ">=" instead of "==", and update '
                f'this assertion on purpose, not by deleting it')
        if prod_price != bench_price:
            failures.append(
                f'N={n_platforms}: liquidation_price_usd differs between modes '
                f'(benchmark={bench_price}, production={prod_price})')

    if failures:
        print('\nFAILED:')
        for f in failures:
            print(' -', f)
        sys.exit(1)
    print('\nOK: liquidation_price_usd identical between benchmark and '
          'production at N=2 and N=3')


if __name__ == '__main__':
    main()
