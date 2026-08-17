"""Runs ONE party's side of a single mascot/field/production MPC
computation. Deliberately does NOT reuse crypto_risk_run.compile_and_run
/mpc_bench.run_protocol: those invoke Scripts/mascot.sh, which spawns
ALL N parties as local subprocesses on one host -- the whole point of
this deployment is that each machine runs only ITS OWN party, talking to
the other over the network. The program itself is never recompiled here
(baked into the image at build time, see node/Dockerfile); this module
only writes this party's own input file, execs mascot-party.x once, and
parses its stdout.

Public-data gathering (price, wallet history -- see risk_data/) and the
scale/guard step are reused as-is from crypto_risk_run.py: same code the
benchmarks already validated, not reimplemented. Only party 0 (the
requester) needs them; party 1 contributes nothing but its own debt
figure and never touches risk_data/oracles at all (it has no reason to
see the client's wallet address or the requester's loan terms).
"""
import logging
import subprocess
import sys
import time
from pathlib import Path

from . import config

sys.path.insert(0, f'{config.MP_SPDZ_HOME}/Scripts')
import crypto_risk_run  # noqa: E402
from risk_data import policy  # noqa: E402

log = logging.getLogger('node.mpc_runner')


class MpcRunError(RuntimeError):
    pass


def _kill_residual_processes():
    """Defense in depth alongside per-request ports (run_party's
    port_base argument, see node/README.md sec:9.6): under normal RQ
    single-concurrency operation this should never find anything -- a
    node's worker can't start a new job until the PREVIOUS job's
    subprocess.run() call has already returned, meaning that job's
    mascot-party.x has already exited. Catches the abnormal case (e.g.
    a worker process crash/restart orphaning a child) instead of
    assuming it can't happen; best-effort only if pgrep isn't
    available."""
    try:
        out = subprocess.run(['pgrep', '-f', 'mascot-party.x'],
                              capture_output=True, text=True)
    except FileNotFoundError:
        return
    pids = [p for p in out.stdout.split() if p]
    for pid in pids:
        log.warning('residual mascot-party.x process (pid %s) found before starting '
                    'a new run -- killing it', pid)
        subprocess.run(['kill', '-9', pid])


def _input_path(party_id):
    return Path(config.MP_SPDZ_HOME) / 'Player-Data' / f'Input-P{party_id}-0'


def write_own_input(payload):
    """payload is this party's slice of the /calcola request body.
    Returns nothing revealed here needs guarding beyond what
    crypto_risk_run.policy.guard already enforces -- same bounds used by
    every benchmark run of this program."""
    debt = policy.guard('existing_debt_usd', int(payload['existing_debt_usd']),
                         0, 1_000_000_000)
    if config.IS_REQUESTER:
        new_loan_usd = policy.guard('new_loan_usd', int(payload['new_loan_usd']),
                                     0, 1_000_000_000)
        raw = crypto_risk_run.gather(
            payload['collateral_asset'], payload['wallet_address'],
            config.CACHE_DIR, payload.get('coingecko_api_key'))
        derived = crypto_risk_run.derive(raw, float(payload['collateral_amount']))
        scaled = crypto_risk_run.scale_and_guard(derived)
        fields = [debt, new_loan_usd, scaled['collateral_value_usd'],
                  scaled['vol_bps'], scaled['corr_bps'],
                  scaled['liquidity_ratio_bps'], scaled['wallet_age_days'],
                  scaled['wallet_flags']]
    else:
        fields = [debt]
    _input_path(config.PARTY_ID).write_text(' '.join(str(f) for f in fields) + '\n')


def run_party(request_id, port_base, raw_log_dir=None):
    """Execs mascot-party.x for THIS party only and blocks until the
    N-party protocol completes (every party must be online simultaneously
    -- see node/README.md). -OF . is not cosmetic: by default
    mascot-party.x only writes the program's print_ln output to stdout
    for party 0 and stays silent for every other party (verified
    directly against this exact binary/program, not assumed from docs)
    -- without it, the other parties would have nothing to parse or
    report.

    port_base is per-REQUEST, assigned by the orchestrator and identical
    across every node for that request -- never config.PORT_BASE (a
    fixed, request-independent default): reusing the same static port
    for every request is exactly what let a fresh request's process
    end up completing a protocol run with a stale, still-alive process
    from a different, unrelated earlier request under back-to-back load
    (node/README.md sec:9.5) -- see sec:9.6 for the fix and its
    verification."""
    _kill_residual_processes()
    cmd = [
        f'./{"mascot-party.x"}',
        '-p', str(config.PARTY_ID),
        '-N', str(config.N_PLATFORMS),
        config.PROG_NAME,
        '-pn', str(port_base),
        '-h', config.PARTY0_HOST,  # required on both parties -- see config.py
        '-e',           # encrypted channels -- see node/README.md for
                         # the certificate exchange this requires
        '-OF', '.',      # stdout on every party, not just party 0
        '-v',            # phase breakdown, same as the benchmark sweeps
    ]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=config.MP_SPDZ_HOME, capture_output=True,
                            text=True, timeout=config.MPC_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - t0
        out = (exc.stdout or b'').decode() if isinstance(exc.stdout, bytes) else (exc.stdout or '')
        err = (exc.stderr or b'').decode() if isinstance(exc.stderr, bytes) else (exc.stderr or '')
        _write_raw_log(raw_log_dir, request_id, out, err, timed_out=True)
        raise MpcRunError(f'timeout dopo {elapsed:.1f}s (budget {config.MPC_TIMEOUT_SECONDS}s)')
    elapsed = time.time() - t0
    _write_raw_log(raw_log_dir, request_id, r.stdout, r.stderr, timed_out=False)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or '')[-800:]
        raise MpcRunError(f'mascot-party.x returncode={r.returncode}: {tail}')
    return r.stdout, elapsed


def _write_raw_log(raw_log_dir, request_id, stdout, stderr, timed_out):
    if not raw_log_dir:
        return
    path = Path(raw_log_dir) / f'{request_id}.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    header = '--- TIMEOUT ---\n' if timed_out else ''
    path.write_text(header + (stdout or '') + '\n--- stderr ---\n' + (stderr or ''))


def parse_result(stdout):
    """Reuses crypto_risk_run's own key:value parser and decision-code
    map -- same semantics as every other place in this repo that reads
    decide_production()'s output.

    Which of esposizione_totale_usd/esposizione_totale (fascia) the .mpc
    program actually printed depends on N_PLATFORMS (exact figure at
    N>=3, band only at N==2 -- see crypto_collateral_risk.mpc's own
    N_PLATFORMS==2 branch), so this mirrors crypto_risk_run.py's own
    _liquidation_price_for: check which key is PRESENT in the output
    rather than branching on config.N_PLATFORMS here, so this stays
    correct even if the two ever disagree (e.g. a mismatched
    MP_SPDZ_PROG override)."""
    mpc_result = crypto_risk_run.parse_output(stdout)
    if 'esito_code' not in mpc_result:
        raise MpcRunError(f"nessun 'esito_code' nell'output -- output grezzo: {stdout[-500:]!r}")
    esito = crypto_risk_run.DECISION_CODE_TO_ESITO.get(int(mpc_result['esito_code']))
    exposure_usd = (int(mpc_result['esposizione_totale_usd'])
                     if 'esposizione_totale_usd' in mpc_result else None)
    fascia = crypto_risk_run._extract_lev_tier(mpc_result) if exposure_usd is None else None
    return {
        'esito': esito,
        'risk_score': int(mpc_result['risk_score']) if 'risk_score' in mpc_result else None,
        'max_ltv_bps': int(mpc_result['max_ltv_bps']) if 'max_ltv_bps' in mpc_result else None,
        'liq_threshold_bps': (int(mpc_result['liq_threshold_bps'])
                                if 'liq_threshold_bps' in mpc_result else None),
        'fascia_esposizione': fascia,
        'esposizione_totale_usd': exposure_usd,
    }
