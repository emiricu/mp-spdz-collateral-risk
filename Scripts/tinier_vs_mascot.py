#!/usr/bin/env python3
"""tinier (crypto_collateral_risk_binary_produzione_sperimentale.mpc,
experimental) vs mascot (crypto_collateral_risk.mpc mode=2 field, the
trusted production reference) -- same rigor as
Scripts/retest_controllato.py's mascot/mama comparison, mirrored
deliberately: same frozen fixture scenario, same alternated order,
same 3 repetitions, same "single dedicated session" discipline. See
that script's own docstring for the confounds this controls for.

mascot side reuses Scripts/crypto_risk_run.py's CLI exactly as
retest_controllato.py does -- already the proven, correct tool for
this. tinier side can't: crypto_risk_run.py's compile_and_run is
hard-wired to mpc_bench.DEFAULT_PROGRAM ('crypto_collateral_risk'),
and mpc_bench.run_protocol's own success regex looks for 'esito:'/
'esito_code:', which the experimental program never prints (it prints
a single packed 'packed: <int>' value instead -- see
crypto_collateral_risk_binary_produzione_sperimentale.mpc's own module
docstring for why, and probe_binary_bitops_2.mpc for the CONVINT bug
that's the reason it can't decode itself before printing). So this
script drives tinier directly: reuses crypto_risk_run.py's gather/
derive/scale_and_guard/write_inputs (same fixture data, same input
files, nothing duplicated) and mpc_bench.compile_domain/build (so
compilation follows the exact same convention as every other program
in this repo), but does its own run+parse for the packed-value output
shape.

Timeout is FIXED at 1200s for every N (not the graduated 600/800/1000/
1200 schedule retest_controllato.py used) -- this run cares about
counting timeouts/crashes as a reliability signal in their own right,
especially at higher N, not just about giving each N a "fair" budget.

Output: results_tinier_vs_mascot/<protocol>_<n>_run<rep>.json (mascot:
full crypto_risk_run.py output; tinier: this script's own equivalent
shape) plus a matching .log/.mpc_raw.log per run and index.json
rebuilt after every run.
"""
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
OUT_ROOT = REPO_ROOT / 'results_tinier_vs_mascot'

sys.path.insert(0, str(REPO_ROOT / 'Scripts'))
import mpc_bench  # noqa: E402
import crypto_risk_run  # noqa: E402

WALLET = '0xclean0000000000000000000000000000000001'
NEW_LOAN = 50000
COLLATERAL_ASSET = 'BTC'
COLLATERAL_AMOUNT = 3.5
TOTAL_EXISTING_DEBT = 20000
N_VALUES = [2, 3, 4, 5]
REPS = 3
TIMEOUT = 1200  # fixed for every N, see module docstring

TINIER_PROGRAM = 'crypto_collateral_risk_binary_produzione_sperimentale'
PACK_BITS = dict(decision=2, score=7, ltv=14, liq=14)  # must match the .mpc's own DECISION_BITS/etc.

_fixture_scaled = None  # populated once, reused for every N/rep -- same
                         # frozen scenario throughout, per instruction


def even_split(total, n):
    base, remainder = divmod(total, n)
    return [base + (1 if i < remainder else 0) for i in range(n)]


def get_fixture_scaled():
    global _fixture_scaled
    if _fixture_scaled is None:
        raw = crypto_risk_run.gather(COLLATERAL_ASSET, WALLET,
                                      '/tmp/crypto_risk_run_cache', price_source='fixture')
        derived = crypto_risk_run.derive(raw, COLLATERAL_AMOUNT)
        _fixture_scaled = crypto_risk_run.scale_and_guard(derived)
    return _fixture_scaled


def decode_packed(packed):
    db, sb, lb, qb = PACK_BITS['decision'], PACK_BITS['score'], PACK_BITS['ltv'], PACK_BITS['liq']
    decision = packed & ((1 << db) - 1)
    score = (packed >> db) & ((1 << sb) - 1)
    ltv = (packed >> (db + sb)) & ((1 << lb) - 1)
    liq = (packed >> (db + sb + lb)) & ((1 << qb) - 1)
    return dict(esito=crypto_risk_run.DECISION_CODE_TO_ESITO.get(decision),
                esito_code=decision, risk_score=score, max_ltv_bps=ltv, liq_threshold_bps=liq)


def run_mascot(n, rep):
    tag = f'mascot_{n}_run{rep}'
    out_path = OUT_ROOT / f'{tag}.json'
    log_path = OUT_ROOT / f'{tag}.log'
    raw_path = OUT_ROOT / f'{tag}.mpc_raw.log'
    debts = even_split(TOTAL_EXISTING_DEBT, n)
    cmd = [sys.executable, 'Scripts/crypto_risk_run.py',
           '--wallet', WALLET, '--platform-debts', ','.join(str(d) for d in debts),
           '--new-loan', str(NEW_LOAN), '--collateral-asset', COLLATERAL_ASSET,
           '--collateral-amount', str(COLLATERAL_AMOUNT),
           '--domain', 'field', '--protocol', 'mascot', '--mode', 'production',
           '--price-source', 'fixture', '--timeout', str(TIMEOUT),
           '--out', str(out_path), '--raw-log', str(raw_path)]
    print(f'  === {tag} (timeout={TIMEOUT}s) ===', flush=True)
    t0 = datetime.datetime.now()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=TIMEOUT + 60, cwd=REPO_ROOT)
        wall = (datetime.datetime.now() - t0).total_seconds()
        log_path.write_text(r.stdout + '\n--- stderr ---\n' + r.stderr)
        ok = r.returncode == 0 and out_path.exists()
        status = 'ok' if ok else f'failed (returncode={r.returncode})'
    except subprocess.TimeoutExpired as exc:
        wall = (datetime.datetime.now() - t0).total_seconds()
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or '')
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or '')
        log_path.write_text(stdout + '\n--- stderr ---\n' + stderr + '\n--- TIMEOUT ---')
        status = 'timeout'

    result = {'tag': tag, 'protocol': 'mascot', 'n_platforms': n, 'rep': rep,
              'status': status, 'wall_clock_seconds': round(wall, 2)}
    if status == 'ok':
        data = json.loads(out_path.read_text())
        result.update(esito=data['esito'], risk_score=data['risk_score'],
                       max_ltv_bps=data['max_ltv_bps'], liq_threshold_bps=data['liq_threshold_bps'],
                       run_time_seconds=data.get('run_time_seconds'),
                       data_sent_mb=data.get('data_sent_mb'))
    print(f'      -> {status}, {wall:.1f}s', flush=True)
    return result


def run_tinier(n, rep):
    tag = f'tinier_{n}_run{rep}'
    log_path = OUT_ROOT / f'{tag}.log'
    raw_path = OUT_ROOT / f'{tag}.mpc_raw.log'
    debts = even_split(TOTAL_EXISTING_DEBT, n)
    scaled = get_fixture_scaled()
    crypto_risk_run.write_inputs(debts, 0, NEW_LOAN, scaled)

    print(f'  === {tag} (timeout={TIMEOUT}s) ===', flush=True)
    compile_log = []
    prog_name = mpc_bench.compile_domain('binary', program=TINIER_PROGRAM,
                                          compile_args=f'{n} 0', mode=0)
    compile_log.append(f'compiled {prog_name}')
    mpc_bench.build(['tinier-party.x'], jobs=4)

    t0 = time.time()
    status = None
    out = ''
    try:
        r = mpc_bench.run(f'PLAYERS={n} Scripts/tinier.sh -v {prog_name}', timeout=TIMEOUT)
        elapsed = time.time() - t0
        out = r.stdout + r.stderr
        m_packed = re.search(r'packed: (\d+)', out)
        status = 'ok' if (r.returncode == 0 and m_packed) else f'failed (returncode={r.returncode})'
    except subprocess.TimeoutExpired as exc:
        elapsed = time.time() - t0
        out = (exc.stdout or '') + (exc.stderr or '')
        status = 'timeout'
        m_packed = None

    raw_path.write_text(out)
    m_data = re.search(r'Global data sent = ([\d.]+) MB', out)
    log_path.write_text('\n'.join(compile_log) + f'\nstatus={status}\n')

    result = {'tag': tag, 'protocol': 'tinier', 'n_platforms': n, 'rep': rep,
              'status': status, 'wall_clock_seconds': round(elapsed, 2)}
    if status == 'ok':
        decoded = decode_packed(int(m_packed.group(1)))
        result.update(decoded, data_sent_mb=float(m_data.group(1)) if m_data else None)
        json.dump(result, open(OUT_ROOT / f'{tag}.json', 'w'), indent=2)
    print(f'      -> {status}, {elapsed:.1f}s', flush=True)
    return result


def check_correctness(mascot_entry, tinier_entry, n):
    if mascot_entry['status'] != 'ok' or tinier_entry['status'] != 'ok':
        return None  # can't compare if either didn't complete
    fields = ('esito', 'risk_score', 'max_ltv_bps', 'liq_threshold_bps')
    mismatches = [f for f in fields if mascot_entry.get(f) != tinier_entry.get(f)]
    ok = not mismatches
    print(f'  [correttezza N={n}] {"OK, coincidono" if ok else "DIVERGENZA: " + str(mismatches)}',
          flush=True)
    if not ok:
        print(f'    mascot: {[(f, mascot_entry.get(f)) for f in fields]}', flush=True)
        print(f'    tinier: {[(f, tinier_entry.get(f)) for f in fields]}', flush=True)
    return ok


def main():
    OUT_ROOT.mkdir(exist_ok=True)
    index = []
    index_path = OUT_ROOT / 'index.json'
    correctness = {}

    print('\n##### FASE 1: verifica di correttezza per ciascun N #####', flush=True)
    for n in N_VALUES:
        tinier_r1 = run_tinier(n, 1)
        mascot_r1 = run_mascot(n, 1)
        index += [tinier_r1, mascot_r1]
        index_path.write_text(json.dumps(index, indent=2))
        correctness[n] = check_correctness(mascot_r1, tinier_r1, n)

    print('\n##### riepilogo correttezza #####', flush=True)
    for n in N_VALUES:
        print(f'  N={n}: {"OK" if correctness[n] else ("NON VERIFICABILE" if correctness[n] is None else "DIVERGENTE")}')

    print('\n##### FASE 2: ripetizioni rimanenti (2 e 3), ordine alternato #####', flush=True)
    total_remaining = (REPS - 1) * len(N_VALUES) * 2
    done = 0
    for rep in range(2, REPS + 1):
        for n in N_VALUES:
            done += 2
            print(f'\n----- [{done}/{total_remaining}] rep={rep} N={n} -----', flush=True)
            tinier_r = run_tinier(n, rep)
            mascot_r = run_mascot(n, rep)
            index += [tinier_r, mascot_r]
            index_path.write_text(json.dumps(index, indent=2))

    summary = {'correctness_by_n': correctness, 'entries': index}
    json.dump(summary, open(OUT_ROOT / 'summary.json', 'w'), indent=2)

    print('\n=== TUTTO COMPLETATO ===')
    for e in index:
        extra = f" esito={e.get('esito')}" if e['status'] == 'ok' else ''
        print(f"{e['tag']:<20} {e['status']:<20} {e['wall_clock_seconds']:>8.1f}s{extra}")


if __name__ == '__main__':
    main()
