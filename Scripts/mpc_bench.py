#!/usr/bin/env python3
"""
Unified benchmark tool for MP-SPDZ protocols against
crypto_collateral_risk.mpc. Single source of truth for the protocol
catalogue and the compile/build/run machinery -- consolidates what used
to be three separate scripts (bench_report.py, scale_bench.py,
fixed_n_bench.py).

Subcommands:
  sweep   every protocol registered for one domain, at a single N
          (its own required N if fixed, otherwise the protocol's script
          default unless --players overrides it)
  scale   a curated set of N-scalable protocols across several N values
  fixed   protocols hard-wired to an exact N: verified via `--help` on
          the binary itself (not just the wrapper script), then run once

Examples:
  ./Scripts/mpc_bench.py sweep --domain field
  ./Scripts/mpc_bench.py sweep --domain binary
  ./Scripts/mpc_bench.py scale --n-list 10,20
  ./Scripts/mpc_bench.py scale --protocols mascot,shamir --n-list 10,20,50
  ./Scripts/mpc_bench.py fixed
"""
import argparse
import os
import re
import signal
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Protocol catalogue: (script, binary, domain, majority, security,
#                      scales_to_arbitrary_n, fixed_n_if_not)
# ---------------------------------------------------------------------------
PROTOCOLS = [
    ('mascot', 'mascot-party.x', 'field', 'dishonest', 'malicious (OT)', True, None),
    ('mama', 'mama-party.x', 'field', 'dishonest', 'malicious (OT)', True, None),
    ('semi', 'semi-party.x', 'field', 'dishonest', 'semi-honest (OT)', True, None),
    ('shamir', 'shamir-party.x', 'field', 'honest', 'semi-honest', True, None),
    ('mal-shamir', 'malicious-shamir-party.x', 'field', 'honest', 'malicious', True, None),
    ('sy-shamir', 'sy-shamir-party.x', 'field', 'honest', 'malicious', True, None),
    ('rep-field', 'replicated-field-party.x', 'field', 'honest', 'semi-honest', False, 3),
    ('mal-rep-field', 'malicious-rep-field-party.x', 'field', 'honest', 'malicious', False, 3),
    ('ps-rep-field', 'ps-rep-field-party.x', 'field', 'honest', 'malicious', False, 3),
    ('sy-rep-field', 'sy-rep-field-party.x', 'field', 'honest', 'malicious', False, 3),
    ('atlas', 'atlas-party.x', 'field', 'honest', 'semi-honest', True, None),
    ('hemi', 'hemi-party.x', 'field', 'dishonest', 'semi-honest (HE)', True, None),
    ('temi', 'temi-party.x', 'field', 'dishonest', 'semi-honest (HE)', True, None),
    ('soho', 'soho-party.x', 'field', 'dishonest', 'semi-honest (HE)', True, None),
    ('lowgear', 'lowgear-party.x', 'field', 'dishonest', 'malicious (HE)', True, None),
    ('highgear', 'highgear-party.x', 'field', 'dishonest', 'malicious (HE)', True, None),
    ('cowgear', 'cowgear-party.x', 'field', 'dishonest', 'covert (HE)', True, None),
    ('chaigear', 'chaigear-party.x', 'field', 'dishonest', 'covert (HE)', True, None),

    ('ring', 'replicated-ring-party.x', 'ring', 'honest', 'semi-honest', False, 3),
    ('semi2k', 'semi2k-party.x', 'ring', 'dishonest', 'semi-honest', True, None),
    ('brain', 'brain-party.x', 'ring', 'honest', 'malicious', False, 3),
    ('mal-rep-ring', 'malicious-rep-ring-party.x', 'ring', 'honest', 'malicious', False, 3),
    ('ps-rep-ring', 'ps-rep-ring-party.x', 'ring', 'honest', 'malicious', False, 3),
    ('sy-rep-ring', 'sy-rep-ring-party.x', 'ring', 'honest', 'malicious', False, 3),
    ('spdz2k', 'spdz2k-party.x', 'ring', 'dishonest', 'malicious', True, None),
    ('rep4-ring', 'rep4-ring-party.x', 'ring', 'honest', 'malicious', False, 4),
    ('dealer-ring', 'dealer-ring-party.x', 'ring', 'dealer', 'semi-honest', True, None),

    ('semi-bin', 'semi-bin-party.x', 'binary', 'dishonest', 'semi-honest', True, None),
    ('tiny', 'tiny-party.x', 'binary', 'dishonest', 'malicious', True, None),
    ('tinier', 'tinier-party.x', 'binary', 'dishonest', 'malicious (cut-and-choose)', True, None),
    ('replicated', 'replicated-bin-party.x', 'binary', 'honest', 'semi-honest', False, 3),
    ('mal-rep-bin', 'malicious-rep-bin-party.x', 'binary', 'honest', 'malicious', False, 3),
    ('ps-rep-bin', 'ps-rep-bin-party.x', 'binary', 'honest', 'malicious', False, 3),
    ('ccd', 'ccd-party.x', 'binary', 'honest', 'semi-honest', True, None),
    ('mal-ccd', 'malicious-ccd-party.x', 'binary', 'honest', 'malicious', True, None),

    ('astra', 'astra-party.x', 'astra', 'special', 'semi-honest (func-dep prep)', False, 3),
    ('trio', 'trio-party.x', 'trio', 'special', 'semi-honest (func-dep prep)', False, 3),
    ('yao', 'yao-party.x', 'yao', 'dishonest', 'malicious (2p GC)', False, 2),
]

# compile.py flag per domain. crypto_collateral_risk.mpc uses only sint
# (no sfix, unlike the earlier program this repo used to benchmark), so
# ring-domain runs need no more than the default 64-bit ring -- verified
# directly: spdz2k-party.x refuses to run 128-bit computation unless the
# whole framework is rebuilt with -DRING_SIZE=128 in CONFIG.mine (a very
# expensive rebuild, since any CONFIG.mine change invalidates make's
# entire dependency graph), but runs fine once the PROGRAM itself is
# compiled for a 64-bit ring instead -- no framework rebuild needed.
# astra/trio still use -R 128 for now (untested at the smaller ring size
# in this pass; their own prep scripts default to 64-bit-ring pipes
# already, so this could likely drop too -- see ensure_pipes below).
DOMAIN_COMPILE = {
    'field': '',
    'ring': '-R 64',
    'binary': '-B 64',
    'astra': '-E astra -R 128',
    'trio': '-E trio -R 128',
    'yao': '-G -B 64',
}

DEFAULT_PROGRAM = 'crypto_collateral_risk'
COMPILE_ARGS = '2 0'  # n_platforms requester -- mode is a separate arg, see compile_domain

# crypto_collateral_risk.mpc's three reveal modes (see its module
# docstring): 0=benchmark (decide_public, all domains), 1=strict
# (decide_strict, field/ring only, thesis case study, not exposed by this
# tool's CLI), 2=production (decide_production, field/ring only).
MODE_BENCHMARK = 0
MODE_STRICT = 1
MODE_PRODUCTION = 2


def run(cmd, timeout=None):
    """shell=True means a plain subprocess.run(..., timeout=...) only
    kills the shell on timeout, not its descendants -- the actual
    <protocol>-party.x processes it exec'd keep running, orphaned,
    silently burning CPU and (worse) holding the compiled program's
    bytecode files open for a DIFFERENT run to later overwrite via a
    same-named recompile. start_new_session=True puts the whole shell
    tree in its own process group so a timeout can kill all of it via
    os.killpg, not just the shell itself."""
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def compile_domain(domain, program=None, compile_args=None, mode=MODE_BENCHMARK):
    if domain == 'binary' and mode != MODE_BENCHMARK:
        sys.exit(
            f'mode={mode} (strict/production) is not supported in the binary '
            f'domain -- both modes compose the decision in secret arithmetic '
            f'before any reveal, which is only proven safe in field/ring, and '
            f'mode=1 additionally needs reveal_to(), unconditionally '
            f'unavailable for binary-circuit types (Compiler/GC/types.py). '
            f'Use --domain field or --domain ring for mode=1/2, or mode=0 '
            f'(the only mode the automated sweeps below ever use) for binary.')
    program = program or DEFAULT_PROGRAM
    compile_args = compile_args or COMPILE_ARGS
    full_args = f'{compile_args} {mode}'
    flag = DOMAIN_COMPILE[domain]
    # compile.py derives its output Bytecode/Schedules filename from the
    # program name + compile-time args ONLY (Compiler/program.py's
    # init_names) -- domain flags like -B/-R/-G never affect it. Without
    # -o, compiling the same (program, args) for two different domains
    # silently overwrites the same file, which can corrupt an unrelated
    # run of a DIFFERENT domain that's mid-execution against that name
    # (this bit a real 'phases' run once -- see git history). -o prefixes
    # the output name with the domain, a real compile.py feature, not a
    # workaround, so field/ring/binary compiles of the same args can
    # never collide.
    prog_name = f'{domain}-{program}-{full_args.replace(" ", "-")}'
    print(f'compiling {prog_name} (domain={domain})...', flush=True)
    r = run(f'./compile.py {flag} -o {domain} {program} {full_args}')
    if r.returncode != 0:
        print(r.stdout[-1500:], r.stderr[-1500:])
        sys.exit(1)
    return prog_name


def build(binaries, jobs=6):
    print(f'building: {" ".join(binaries)}', flush=True)
    r = run(f'make -j{jobs} -k {" ".join(binaries)}', timeout=3600)
    print(r.stdout[-2000:], r.stderr[-2000:])


def ensure_pipes(domain):
    """astra/trio's own setup script only pre-creates named pipes for a
    64-bit ring; our -R 128 needs the same layout for its own ring size."""
    dirn = f'Player-Data/3-{domain}-R-128'
    run(f'mkdir -p {dirn}')
    if 'Protocol-P0-T0' not in run(f'ls {dirn}').stdout:
        for t in range(101):
            for kind in ('Protocol', 'Outputs'):
                for edabit in ('', 'edaBits-'):
                    for p in range(3):
                        run(f'mkfifo {dirn}/{kind}-{edabit}P{p}-T{t}')


def run_protocol(script, prog_name, n=None, timeout=300, domain=None, verbose=False):
    prefix = f'PLAYERS={n} ' if n else ''
    if domain in ('astra', 'trio'):
        prefix += 'PORT=15000 '
        ensure_pipes(domain)
    flag = '-v ' if verbose else ''
    t0 = time.time()
    try:
        r = run(f'{prefix}Scripts/{script}.sh {flag}{prog_name}', timeout=timeout)
    except subprocess.TimeoutExpired:
        return 'timeout', None, None, ''
    elapsed = round(time.time() - t0, 2)
    out = r.stdout + r.stderr
    # mode=1/strict (decide_strict) and mode=2/production (decide_production)
    # never print 'esito:' by design -- only a numeric code -- so their
    # completion markers are recognized too.
    m_dec = (re.search(r'esito: (.+)', out)
             or re.search(r'esito_code_strict: (\d+)', out)
             or re.search(r'esito_code: (\d+)', out))
    m_data = re.search(r'Global data sent = ([\d.]+) MB', out)
    status = 'ok' if r.returncode == 0 and m_dec else 'failed'
    return status, elapsed, (float(m_data.group(1)) if m_data else None), out


# MP-SPDZ's own -v (verbose) instrumentation, not something this tool
# derives itself: "online phase" is the data-dependent part (real inputs,
# actual protocol rounds on them); "preprocessing/offline phase" is the
# data-independent part (Beaver triples, edaBits, MACs, ... -- correlated
# randomness that could be generated before any real input is known, or
# amortized across many future runs). The offline side's "(X MB, Y
# rounds, ...)" detail is sometimes absent (e.g. yao) -- made optional.
PHASE_RE = re.compile(
    r'Spent ([\d.e+-]+) seconds \(([\d.e+-]+) MB, (\d+) rounds, [\d.e+-]+ CPU seconds\) '
    r'on the online phase and ([\d.e+-]+) seconds'
    r'(?: \(([\d.e+-]+) MB, (\d+) rounds, [\d.e+-]+ CPU seconds\))? '
    r'on the preprocessing/offline phase\.'
)


def parse_phase_breakdown(out):
    """Returns None if the -v breakdown line isn't present (protocol
    didn't report it, or run wasn't verbose)."""
    m = PHASE_RE.search(out)
    if not m:
        return None
    online_s, online_mb, online_rounds, prep_s, prep_mb, prep_rounds = m.groups()
    return dict(
        online_time=float(online_s), online_mb=float(online_mb), online_rounds=int(online_rounds),
        prep_time=float(prep_s),
        prep_mb=float(prep_mb) if prep_mb is not None else 0.0,
        prep_rounds=int(prep_rounds) if prep_rounds is not None else 0,
    )


def print_table(rows, columns):
    widths = [max(len(str(r[i])) for r in ([columns] + rows)) + 2
              for i in range(len(columns))]
    fmt = lambda row: ''.join(str(v).ljust(w) for v, w in zip(row, widths))
    print(fmt(columns))
    print('-' * sum(widths))
    for row in rows:
        print(fmt(row))


def group_by_domain(protocols):
    by_domain = {}
    for p in protocols:
        by_domain.setdefault(p[2], []).append(p)
    return by_domain


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------
def cmd_sweep(args):
    protos = [p for p in PROTOCOLS if p[2] == args.domain]
    if not protos:
        sys.exit(f'no protocols registered for domain={args.domain}')
    prog_name = compile_domain(args.domain, args.program, args.compile_args, args.mode)
    build([p[1] for p in protos], args.jobs)

    rows = []
    for name, binary, domain, majority, security, scales, fixed_n in protos:
        n = args.players or fixed_n
        print(f'--- {name} (N={n or "default"}) ---', flush=True)
        status, t, mb, out = run_protocol(name, prog_name, n, args.run_timeout, domain)
        if status != 'ok':
            print(out[-1200:])
        rows.append((name, majority, security, status, t, mb))
    print()
    print_table(rows, ('protocol', 'majority', 'security', 'status', 'time(s)', 'data(MB)'))


# ---------------------------------------------------------------------------
# phases -- online (data-dependent) vs preprocessing/offline
# (data-independent) time+data breakdown, via -v. The number that matters
# for blockchain feasibility is the ONLINE side: preprocessing can be run
# continuously/proactively by the platforms, off the critical path of any
# specific on-chain-triggered request, so it never has to sit in a user's
# latency budget the way the online phase does.
# ---------------------------------------------------------------------------
def cmd_phases(args):
    protos = [p for p in PROTOCOLS if p[2] == args.domain]
    if args.protocols:
        names = set(args.protocols.split(','))
        protos = [p for p in protos if p[0] in names]
    if not protos:
        sys.exit(f'no protocols registered for domain={args.domain}')
    prog_name = compile_domain(args.domain, args.program, args.compile_args, args.mode)
    build([p[1] for p in protos], args.jobs)

    rows = []
    for name, binary, domain, majority, security, scales, fixed_n in protos:
        n = args.players or fixed_n
        print(f'--- {name} (N={n or "default"}) ---', flush=True)
        status, t, mb, out = run_protocol(name, prog_name, n, args.run_timeout,
                                           domain, verbose=True)
        if status != 'ok':
            print(out[-1200:])
            rows.append((name, majority, security, status, '-', '-', '-', '-', '-'))
            continue
        phases = parse_phase_breakdown(out)
        if phases is None:
            rows.append((name, majority, security, status, '-', '-', 'n/a', '-', '-'))
            continue
        total = phases['prep_time'] + phases['online_time']
        prep_pct = round(100 * phases['prep_time'] / total, 1) if total > 0 else None
        rows.append((name, majority, security, status,
                      round(phases['prep_time'], 4), round(phases['online_time'], 4),
                      prep_pct, round(phases['prep_mb'], 4), round(phases['online_mb'], 4)))
    print()
    print_table(rows, ('protocol', 'majority', 'security', 'status',
                        'prep(s)', 'online(s)', 'prep %', 'prep(MB)', 'online(MB)'))


# ---------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------
def cmd_scale(args):
    names = (args.protocols.split(',') if args.protocols else
             ['mascot', 'semi', 'hemi', 'shamir', 'mal-shamir',
              'dealer-ring', 'ccd', 'mal-ccd'])
    n_list = [int(x) for x in args.n_list.split(',')]
    selected = [p for p in PROTOCOLS if p[0] in names]

    rows = []
    for domain, protos in group_by_domain(selected).items():
        prog_name = compile_domain(domain, args.program, args.compile_args, args.mode)
        build([p[1] for p in protos], args.jobs)
        for name, binary, dom, majority, security, scales, fixed_n in protos:
            if not scales:
                print(f'--- skipping {name}: hard-wired to N={fixed_n}, '
                      f'not scalable (use the "fixed" subcommand) ---')
                continue
            for n in n_list:
                print(f'--- {name} N={n} ---', flush=True)
                status, t, mb, out = run_protocol(name, prog_name, n,
                                                   args.run_timeout, dom)
                if status != 'ok':
                    print(out[-800:])
                rows.append((name, majority, security, n, status, t, mb))
    print()
    print_table(rows, ('protocol', 'majority', 'security', 'N', 'status',
                        'time(s)', 'data(MB)'))


# ---------------------------------------------------------------------------
# fixed
# ---------------------------------------------------------------------------
def cmd_fixed(args):
    fixed_protos = [p for p in PROTOCOLS if p[5] is False]
    rows = []
    for domain, protos in group_by_domain(fixed_protos).items():
        prog_name = compile_domain(domain, args.program, args.compile_args, args.mode)
        build([p[1] for p in protos], args.jobs)
        for name, binary, dom, majority, security, scales, fixed_n in protos:
            help_out = run(f'./{binary} --help').stdout
            has_n_flag = bool(re.search(r'-N,|--nparties', help_out))
            print(f'--- {name}: required N={fixed_n}, -N flag in binary? '
                  f'{has_n_flag} ---', flush=True)
            status, t, mb, out = run_protocol(name, prog_name, fixed_n,
                                               args.run_timeout, dom)
            if status != 'ok':
                print(out[-800:])
            rows.append((name, majority, f'N={fixed_n}', has_n_flag, status, t))
    print()
    print_table(rows, ('protocol', 'majority', 'required N', 'has -N flag',
                        'status', 'time(s)'))


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--program', default=None,
                    help='override the .mpc source name for this domain')
    p.add_argument('--compile-args', default=None,
                    help='override the compile-time args (default: '
                         f'{COMPILE_ARGS!r})')
    p.add_argument('--mode', type=int, default=MODE_BENCHMARK,
                    choices=[MODE_BENCHMARK, MODE_STRICT, MODE_PRODUCTION],
                    help='0=benchmark (default, all domains), 1=strict '
                         '(field/ring only), 2=production (field/ring only)')
    p.add_argument('--jobs', type=int, default=6, help='parallel make jobs')
    p.add_argument('--run-timeout', type=int, default=300)
    sub = p.add_subparsers(dest='cmd', required=True)

    sp = sub.add_parser('sweep', help='every protocol in one domain, single N')
    sp.add_argument('--domain', required=True, choices=sorted(DOMAIN_COMPILE))
    sp.add_argument('--players', type=int, default=None)
    sp.set_defaults(func=cmd_sweep)

    ph = sub.add_parser('phases', help='online vs preprocessing/offline time+data breakdown (-v)')
    ph.add_argument('--domain', required=True, choices=sorted(DOMAIN_COMPILE))
    ph.add_argument('--players', type=int, default=None)
    ph.add_argument('--protocols', default=None,
                     help='comma-separated protocol names; default: all in --domain')
    ph.set_defaults(func=cmd_phases)

    sc = sub.add_parser('scale', help='N-scalable protocols across several N')
    sc.add_argument('--protocols', default=None,
                     help='comma-separated protocol names; default: a curated set')
    sc.add_argument('--n-list', default='10,20')
    sc.set_defaults(func=cmd_scale)

    fx = sub.add_parser('fixed', help='protocols hard-wired to an exact N')
    fx.set_defaults(func=cmd_fixed)

    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
