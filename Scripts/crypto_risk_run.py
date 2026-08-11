#!/usr/bin/env python3
"""
Orchestrator for the crypto-collateral risk demo: gathers public market /
on-chain data, derives and scales the metrics
Programs/Source/crypto_collateral_risk.mpc expects, writes the
per-platform Input-P<i>-0 files, runs the MPC computation via
mpc_bench's own compile/build/run machinery, and merges the revealed
output with the public context into one JSON result.

Price data is ALWAYS live (CoinGecko), never provided by a participating
platform: if the requesting platform could self-report the collateral
price, it could inflate or deflate it to bias the outcome in its own
favor (make a risky loan look safe, or vice versa to hurt a competitor)
-- exactly the manipulation vector price oracles like Chainlink exist to
remove in DeFi generally. CoinGecko aggregates across many exchanges, so
no single participating platform controls it. Fetched at the start of
each run and cached for 2 minutes (see risk_data/cache.py) -- fresh
enough to reflect near-real-time price, cheap enough not to hammer the
API when several runs (e.g. a scaling study across N) happen back to
back.

Wallet on-chain history (age, tx count, KYC-exchange/mixer signals) is
still read from the fixture layer, NOT live: the same manipulation
argument applies just as much (a self-reporting platform could lie about
a risky wallet being clean), but the live path (Etherscan) needs a paid
API key this environment doesn't have yet. Documented here as a known,
open limitation -- not silently worked around -- see EvmWalletAnalyzer
in risk_data/wallets.py, which raises immediately if used without a key.

Two modes (--mode), matching the .mpc's own (see its module docstring):
  benchmark (default): full detail -- every sub-score, both policy
      thresholds, the whole public context -- unchanged shape from
      before this had a --mode flag at all. Not meant to be signed or
      published anywhere; a diagnostic/thesis view.
  production: only what MODE_PRODUCTION reveals (decision, total score,
      max_ltv_bps, plus the same N==2-band/N>=3-exact exposure rule as
      benchmark) -- shaped as the payload the N participating platforms
      would co-sign (see merge_production/build_onchain_payload for the
      distinction between what the platforms share and what actually
      goes on the public chain).

MODE_STRICT (code-only, reveal_to(REQUESTER) alone) is deliberately NOT
a --mode choice here: it hides the outcome from every platform but the
requester, which is incompatible with this orchestrator's job of
producing something the N platforms can jointly sign. It remains
reachable manually (./compile.py ... crypto_collateral_risk <n> <r> 1),
documented in the .mpc as a thesis case study on the cost of maximal
privacy, not a production path.

Usage:
  ./Scripts/crypto_risk_run.py \\
      --wallet 0xclean0000000000000000000000000000000001 \\
      --platform-debts 0,0 --new-loan 50000 \\
      --collateral-asset BTC --collateral-amount 3.5 \\
      --domain field --protocol semi --mode production
"""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mpc_bench  # noqa: E402
from risk_data import oracles, wallets, metrics, policy, cache, signing  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
PLAYER_DATA = REPO_ROOT / 'Player-Data'

# Correlation is measured against this benchmark asset, not the client's
# collateral on other platforms -- see the plan's scope note on why a
# second genuinely-MPC correlation ingredient was left out to keep scope
# bounded.
BENCHMARK_ASSET = 'BTC'

DECISION_CODE_TO_ESITO = {0: 'RIFIUTATO', 1: 'REVIEW MANUALE', 2: 'APPROVATO'}

# A risk score attached permanently and publicly to a wallet address on
# the chain itself is a different, longer-lived data exposure than
# sharing it only with the N participating platforms -- especially if
# the wallet is later linkable to a KYC'd identity (right-to-be-forgotten
# tension). Explicit, commented parameter rather than a silent choice
# buried in build_onchain_payload() -- see its docstring.
PUBLISH_RISK_SCORE_ONCHAIN = False


def _extract_lev_tier(mpc_result):
    """N==2 responses print 'esposizione_totale: fascia <N> (...)' -- pull
    just the tier digit back out for policy.liquidation_price_usd()."""
    band = mpc_result.get('esposizione_totale')
    if band is None:
        return None
    m = re.search(r'fascia (\d+)', band)
    return int(m.group(1)) if m else None


PRICE_CACHE_TTL_SECONDS = 120  # ~2 minutes -- see module docstring


# ---------------------------------------------------------------------------
# Step 1: gather -- price is always live (anti-manipulation, see module
# docstring); wallet history is fixture-only for now (no Etherscan key)
# ---------------------------------------------------------------------------
def gather(collateral_asset, wallet_address, cache_dir, coingecko_api_key=None):
    price_cache = cache.Cache(cache_dir, ttl_seconds=PRICE_CACHE_TTL_SECONDS)
    price_oracle = oracles.CoinGeckoOracle(cache=price_cache, api_key=coingecko_api_key)
    wallet_analyzer = wallets.FixtureWalletAnalyzer()

    address_book = wallets.AddressBook.from_fixture()

    collateral_history = price_oracle.history(collateral_asset, days=90)
    benchmark_history = (collateral_history if collateral_asset == BENCHMARK_ASSET
                          else price_oracle.history(BENCHMARK_ASSET, days=90))

    return dict(
        price_oracle_provenance=price_oracle.provenance,
        wallet_provenance=wallet_analyzer.provenance,
        collateral_history=collateral_history,
        benchmark_history=benchmark_history,
        spot_usd=price_oracle.spot(collateral_asset),
        volume_24h_usd=price_oracle.volume_24h(collateral_asset),
        wallet_profile=wallet_analyzer.analyze(wallet_address),
        address_book=address_book,
    )


# ---------------------------------------------------------------------------
# Step 2: derive -- pure calculation, no I/O
# ---------------------------------------------------------------------------
def derive(raw, collateral_amount):
    collateral_value_usd = raw['spot_usd'] * collateral_amount
    collateral_returns = metrics.daily_returns(raw['collateral_history'])
    benchmark_returns = metrics.daily_returns(raw['benchmark_history'])
    wallet_signals = wallets.wallet_risk_signals(raw['wallet_profile'], raw['address_book'])
    return dict(
        collateral_value_usd=collateral_value_usd,
        volatility_annualized=metrics.volatility(collateral_returns),
        correlation_vs_benchmark=metrics.correlation(collateral_returns, benchmark_returns),
        liquidity_ratio=metrics.liquidity_ratio(raw['volume_24h_usd'], collateral_value_usd),
        wallet_age_days=wallet_signals['wallet_age_days'],
        wallet_flags=wallet_signals['wallet_flags'],
    )


# ---------------------------------------------------------------------------
# Step 3: scale -- basis-point/whole-USD integers, range-checked while
# still in the clear (see policy.guard's docstring for why this matters)
# ---------------------------------------------------------------------------
def scale_and_guard(derived):
    return dict(
        collateral_value_usd=policy.guard(
            'collateral_value_usd', policy.to_usd_int(derived['collateral_value_usd']),
            1, 10_000_000_000),
        vol_bps=policy.guard(
            'vol_bps', policy.to_bps(derived['volatility_annualized']), 0, 10 * policy.BPS),
        corr_bps=policy.guard(
            'corr_bps', policy.to_bps(abs(derived['correlation_vs_benchmark'])), 0, policy.BPS),
        liquidity_ratio_bps=policy.guard(
            # deep, liquid markets (e.g. a modest BTC position against
            # BTC's own ~$25B daily volume) can legitimately be 1e4x+ --
            # the .mpc side only cares whether this clears the "good"
            # tier (LIQUIDITY_GOOD_BPS), so the ceiling here is a sanity
            # check against garbage input, not a realistic-range bound
            'liquidity_ratio_bps', policy.to_bps(derived['liquidity_ratio']), 0, 10**10),
        wallet_age_days=policy.guard(
            'wallet_age_days', int(derived['wallet_age_days']), 0, 100_000),
        wallet_flags=policy.guard(
            'wallet_flags', int(derived['wallet_flags']), 0, 100),
    )


# ---------------------------------------------------------------------------
# Step 4: write -- Player-Data/Input-P<i>-0, in the exact order
# Programs/Source/crypto_collateral_risk.mpc reads them (its module
# docstring is the authoritative contract)
# ---------------------------------------------------------------------------
def write_inputs(platform_debts, requester, new_loan_usd, scaled):
    """Guards every value BEFORE writing anything: a mid-loop guard
    failure would otherwise leave Player-Data partially overwritten with
    this run's data for some parties and the previous run's for others."""
    n_platforms = len(platform_debts)
    platform_debts = [policy.guard(f'platform_debts[{i}]', int(debt), 0, 1_000_000_000)
                       for i, debt in enumerate(platform_debts)]
    for i, debt in enumerate(platform_debts):
        path = PLAYER_DATA / f'Input-P{i}-0'
        if i == requester:
            fields = [debt, new_loan_usd, scaled['collateral_value_usd'],
                      scaled['vol_bps'], scaled['corr_bps'],
                      scaled['liquidity_ratio_bps'], scaled['wallet_age_days'],
                      scaled['wallet_flags']]
        else:
            fields = [debt]
        path.write_text(' '.join(str(f) for f in fields) + '\n')
    return n_platforms


# ---------------------------------------------------------------------------
# Step 5: compile+run -- reuse mpc_bench's own machinery, don't duplicate it
# ---------------------------------------------------------------------------
def compile_and_run(protocol, domain, n_platforms, requester, mode, timeout, jobs):
    proto = next((p for p in mpc_bench.PROTOCOLS if p[0] == protocol), None)
    if proto is None:
        sys.exit(f'unknown protocol {protocol!r} -- see mpc_bench.PROTOCOLS')
    _, binary, proto_domain, _, _, scales, fixed_n = proto
    if proto_domain != domain:
        sys.exit(f'protocol {protocol!r} is registered for domain '
                  f'{proto_domain!r}, not {domain!r}')
    if not scales and fixed_n != n_platforms:
        sys.exit(f'protocol {protocol!r} is hard-wired to N={fixed_n}, '
                  f'but this run has {n_platforms} platforms')

    # compile_domain itself sys.exits with a clear message if mode!=0 is
    # requested together with the binary domain -- not duplicated here.
    compile_args = f'{n_platforms} {requester}'
    prog_name = mpc_bench.compile_domain(domain, compile_args=compile_args, mode=mode)
    mpc_bench.build([binary], jobs=jobs)
    # verbose=True: negligible overhead, and gives the online-vs-
    # preprocessing/offline split for THIS specific scenario's real data
    # (see phase_breakdown in the merged result) -- the number that
    # matters for blockchain feasibility is the online side alone, since
    # preprocessing can run continuously/proactively, off the critical
    # path of any specific on-chain-triggered request.
    status, elapsed, data_mb, out = mpc_bench.run_protocol(
        protocol, prog_name, n=n_platforms, timeout=timeout, domain=domain, verbose=True)
    if status != 'ok':
        print(out[-2000:], file=sys.stderr)
        sys.exit(f'MPC run failed: status={status}')
    return out, elapsed, data_mb


# ---------------------------------------------------------------------------
# Step 6: parse -- every line the .mpc program prints is 'key: value'
# ---------------------------------------------------------------------------
def parse_output(out):
    result = {}
    for line in out.splitlines():
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        key, value = key.strip(), value.strip()
        if key and value and key.replace('_', '').isalnum():
            result[key] = value
    return result


def _liquidation_price_for(mpc_result, scaled, args):
    """Shared by both merge_* below: N-aware (exact at N>=3, conservative
    at N==2 -- see policy.liquidation_price_usd's docstring), using
    whatever of {exposure_usd, liq_threshold_bps, lev_tier} the current
    mode actually revealed. Returns (price_or_None, note_or_None)."""
    if 'max_ltv_bps' not in mpc_result:
        return None, 'max_ltv_bps not present in this output'
    exposure_usd = (int(mpc_result['esposizione_totale_usd'])
                     if 'esposizione_totale_usd' in mpc_result else None)
    lev_tier = _extract_lev_tier(mpc_result) if exposure_usd is None else None
    liq_threshold_bps = (int(mpc_result['liq_threshold_bps'])
                          if 'liq_threshold_bps' in mpc_result else None)
    try:
        return policy.liquidation_price_usd(
            collateral_amount=args.collateral_amount,
            max_ltv_bps=int(mpc_result['max_ltv_bps']),
            collateral_value_usd=scaled['collateral_value_usd'],
            exposure_usd=exposure_usd,
            liq_threshold_bps=liq_threshold_bps,
            lev_tier=lev_tier)
    except policy.PolicyError as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Step 7a: merge (benchmark mode) -- revealed MPC fields + public context
# that never entered MPC + the liquidation price. Unchanged shape from
# before this file had a --mode flag: a diagnostic/thesis view, not meant
# to be signed or published.
# ---------------------------------------------------------------------------
def merge_benchmark(mpc_result, scaled, derived, raw, args, elapsed, data_mb):
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    liquidation_price_usd, liquidation_price_note = _liquidation_price_for(
        mpc_result, scaled, args)

    return {
        'wallet_address': args.wallet,
        'policy_version': int(mpc_result.get('policy_version', -1)),
        'timestamp': timestamp,
        'n_platforms': int(mpc_result.get('piattaforme', len(args.platform_debts_list))),
        'requester_platform': args.requester,
        'domain': args.domain,
        'protocol': args.protocol,
        'run_time_seconds': elapsed,
        'data_sent_mb': data_mb,
        'esito': mpc_result.get('esito'),
        'risk_score': int(mpc_result['risk_score']) if 'risk_score' in mpc_result else None,
        'mpc_output': mpc_result,
        'public_context': {
            'collateral_asset': args.collateral_asset,
            'collateral_amount': args.collateral_amount,
            'collateral_value_usd': scaled['collateral_value_usd'],
            'spot_price_usd': raw['spot_usd'],
            'price_provenance': raw['price_oracle_provenance'],
            'benchmark_asset': BENCHMARK_ASSET,
            'volatility_annualized': derived['volatility_annualized'],
            'correlation_vs_benchmark': derived['correlation_vs_benchmark'],
            'liquidity_ratio': derived['liquidity_ratio'],
            'wallet_provenance': raw['wallet_provenance'],
            'wallet_age_days': derived['wallet_age_days'],
            'wallet_flags': derived['wallet_flags'],
        },
        'liquidation_price_usd': liquidation_price_usd,
        'liquidation_price_note': liquidation_price_note,
    }


def build_signable_payload(result):
    """Benchmark mode's illustrative signable tuple -- kept for
    continuity, but note this mode also prints every sub-score in
    mpc_output, so it is NOT the RECOMMENDED payload for a real
    deployment to publish, since it doesn't hide the sub-scores from
    competing platforms (see merge_production/build_onchain_payload
    below for the reduced payload production mode signs instead). Still
    signed for REAL here, though -- same signing.py machinery, same
    per-platform persisted keys as production mode -- not a placeholder
    shape with null signatures."""
    payload = {
        'wallet_address': result['wallet_address'],
        'esito': result['esito'],
        'policy_version': result['policy_version'],
        'timestamp': result['timestamp'],
    }
    payload['participants'] = [
        {'platform_id': i, 'signature': signing.sign_payload(i, payload)}
        for i in range(result['n_platforms'])
    ]
    return payload


# ---------------------------------------------------------------------------
# Step 7b: merge (production mode) -- ONLY what MODE_PRODUCTION reveals:
# decision, total score, max_ltv_bps, liq_threshold_bps, and the exposure
# figure/band. The keys through 'participants' are exactly what the N
# participating platforms would co-sign (though only a reduced subset
# actually reaches the public chain -- see build_onchain_payload);
# 'onchain_payload' and the liquidation-price fields are orchestrator-side
# extras, appended after and NOT part of what gets signed.
# ---------------------------------------------------------------------------
def merge_production(mpc_result, scaled, derived, raw, args, elapsed, data_mb):
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    esito = (DECISION_CODE_TO_ESITO.get(int(mpc_result['esito_code']))
              if 'esito_code' in mpc_result else None)
    n_platforms = len(args.platform_debts_list)

    # decide_production() reveals the exposure itself (exact at N>=3, band
    # only at N==2 -- same rule as decide_public, no exception) via
    # 'esposizione_totale_usd'/'esposizione_totale' in the raw MPC output
    # (Programs/Source/crypto_collateral_risk.mpc's own print_ln calls).
    # This was parsed into mpc_result and already used internally by
    # _liquidation_price_for(), but never surfaced in shared_payload --
    # silently dropping data the protocol itself already made public to
    # all N parties, the same class of gap run_time_seconds/data_sent_mb
    # had above.
    exposure_usd = (int(mpc_result['esposizione_totale_usd'])
                     if 'esposizione_totale_usd' in mpc_result else None)
    lev_tier = _extract_lev_tier(mpc_result) if exposure_usd is None else None

    shared_payload = {
        'wallet_address': args.wallet,
        'policy_version': int(mpc_result.get('policy_version', -1)),
        'timestamp': timestamp,
        'n_platforms': n_platforms,
        'esito': esito,
        'risk_score': int(mpc_result['risk_score']) if 'risk_score' in mpc_result else None,
        'max_ltv_bps': int(mpc_result['max_ltv_bps']) if 'max_ltv_bps' in mpc_result else None,
        'liq_threshold_bps': (int(mpc_result['liq_threshold_bps'])
                                if 'liq_threshold_bps' in mpc_result else None),
        'esposizione_totale_usd': exposure_usd,
        'esposizione_fascia': lev_tier,
        'participants': [{'platform_id': i, 'signature': None} for i in range(n_platforms)],
    }

    liquidation_price_usd, liquidation_price_note = _liquidation_price_for(
        mpc_result, scaled, args)
    shared_payload['liquidation_price_usd'] = liquidation_price_usd
    shared_payload['liquidation_price_note'] = liquidation_price_note
    # Orchestrator-side telemetry, appended after 'participants' and NOT
    # part of what gets signed (see build_onchain_payload) -- run time and
    # data volume are facts about this specific execution, not about the
    # loan decision itself. merge_benchmark's return already carries the
    # same two fields; production silently dropped them until now even
    # though compile_and_run() always computes them.
    shared_payload['run_time_seconds'] = elapsed
    shared_payload['data_sent_mb'] = data_mb
    shared_payload['onchain_payload'] = build_onchain_payload(shared_payload)
    # Mirror the real signatures produced for the on-chain tuple back onto
    # each participant entry -- same tuple, same signature, two views.
    for i, sig in enumerate(shared_payload['onchain_payload']['signatures']):
        shared_payload['participants'][i]['signature'] = sig
    return shared_payload


# ---------------------------------------------------------------------------
# Step 8: publish. build_onchain_payload is what actually reaches the
# public chain, reduced further from shared_payload (what the N
# platforms exchange among themselves, step 7b above): no
# risk_score/max_ltv_bps by default (see PUBLISH_RISK_SCORE_ONCHAIN).
#
# Signatures ARE real: each platform i (identified only by its index
# here -- see Scripts/risk_data/signing.py for what "identity" means in
# this demo) signs the exact same tuple below with its own persisted
# ECDSA key (secp256k1) and the signature is verified against that
# platform's own public key before being accepted, so a broken signature
# fails loudly here rather than silently reaching the "chain". The
# multisig/threshold CONTRACT itself -- collecting these signatures on-
# chain and publishing only at quorum -- is still a separate piece of
# engineering, not built here; what's real is the cryptography, not the
# contract.
# ---------------------------------------------------------------------------
def build_onchain_payload(shared_payload):
    payload = {
        'wallet_address': shared_payload['wallet_address'],
        'esito': shared_payload['esito'],
        'policy_version': shared_payload['policy_version'],
        'timestamp': shared_payload['timestamp'],
    }
    if PUBLISH_RISK_SCORE_ONCHAIN:
        payload['risk_score'] = shared_payload['risk_score']
        payload['max_ltv_bps'] = shared_payload['max_ltv_bps']

    signatures = []
    for platform_id in range(shared_payload['n_platforms']):
        sig = signing.sign_payload(platform_id, payload)
        if not signing.verify_signature(platform_id, payload, sig):
            # Would mean this platform's own signature doesn't verify
            # against its own just-used key -- a bug here, not a
            # malicious-party scenario (this loop signs with every
            # platform's key locally; a real deployment would instead
            # collect independently-produced signatures from N separate
            # machines and verify each against its known public key).
            raise RuntimeError(f'platform {platform_id} signature failed '
                                f'self-verification -- this should never happen')
        signatures.append(sig)

    payload['signatures'] = signatures
    return payload


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--wallet', required=True, help='client wallet address (on-chain lookup key)')
    p.add_argument('--platform-debts', required=True,
                    help='comma-separated existing debt (USD) per platform, one per party')
    p.add_argument('--requester', type=int, default=0,
                    help='index into --platform-debts of the requesting platform; must be 0 '
                         '-- only party 0 stdout reaches this harness (Scripts/run-common.sh)')
    p.add_argument('--new-loan', type=float, required=True, help='USD, the loan being underwritten')
    p.add_argument('--collateral-asset', default='BTC')
    p.add_argument('--collateral-amount', type=float, required=True)
    p.add_argument('--cache-dir', default='/tmp/crypto_risk_run_cache',
                    help='price cache directory (2-minute TTL, see PRICE_CACHE_TTL_SECONDS)')
    p.add_argument('--coingecko-api-key', default=os.environ.get('COINGECKO_API_KEY'),
                    help='optional, raises the rate limit; the free tier works without one '
                         '(default: $COINGECKO_API_KEY)')
    p.add_argument('--domain', default='field', choices=sorted(mpc_bench.DOMAIN_COMPILE))
    p.add_argument('--protocol', default='semi')
    p.add_argument('--mode', default='benchmark', choices=['benchmark', 'production'],
                    help="'benchmark': full detail, diagnostic/thesis view (default). "
                         "'production': only decision+score+max_ltv_bps, shaped for "
                         "N-platform co-signing (see merge_production). MODE_STRICT "
                         "(code-only, requester-only) is not offered here -- see the "
                         "module docstring.")
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--timeout', type=int, default=300)
    p.add_argument('--out', default=None, help='write the final JSON here (default: stdout)')
    p.add_argument('--raw-log', default=None,
                    help="write the underlying MPC run's raw stdout/stderr here (compile "
                         "counts, -v phase breakdown, everything parse_output() reduces to "
                         "'key: value' pairs) -- not written by default since it's verbose "
                         "and redundant with --out for normal use")
    args = p.parse_args()

    if args.requester != 0:
        sys.exit('--requester must be 0: only party 0 stdout reaches the '
                  'run harness (Scripts/run-common.sh), so the requesting '
                  'platform must be party 0 for its output to be visible here')

    platform_debts = [int(x) for x in args.platform_debts.split(',')]
    args.platform_debts_list = platform_debts
    if not (0 <= args.requester < len(platform_debts)):
        sys.exit(f'--requester {args.requester} out of range for '
                  f'{len(platform_debts)} platforms')
    new_loan_usd = policy.guard('new_loan_usd', int(args.new_loan), 0, 1_000_000_000)

    mode = {'benchmark': mpc_bench.MODE_BENCHMARK,
             'production': mpc_bench.MODE_PRODUCTION}[args.mode]

    raw = gather(args.collateral_asset, args.wallet, args.cache_dir, args.coingecko_api_key)
    derived = derive(raw, args.collateral_amount)
    scaled = scale_and_guard(derived)
    n_platforms = write_inputs(platform_debts, args.requester, new_loan_usd, scaled)
    out, elapsed, data_mb = compile_and_run(
        args.protocol, args.domain, n_platforms, args.requester, mode,
        args.timeout, args.jobs)
    if args.raw_log:
        Path(args.raw_log).write_text(out)
    mpc_result = parse_output(out)

    if args.mode == 'benchmark':
        result = merge_benchmark(mpc_result, scaled, derived, raw, args, elapsed, data_mb)
        result['signable_payload'] = build_signable_payload(result)
    else:
        result = merge_production(mpc_result, scaled, derived, raw, args, elapsed, data_mb)

    # Online (data-dependent: this run's real inputs) vs
    # preprocessing/offline (data-independent: correlated randomness that
    # could be generated before this specific request was even known)
    # for THIS scenario -- see mpc_bench.cmd_phases for the same
    # breakdown swept across the whole protocol catalogue. The number
    # that matters for blockchain feasibility is online_time: prep can
    # run continuously/proactively, off the critical path of an
    # on-chain-triggered request.
    phases = mpc_bench.parse_phase_breakdown(out)
    if phases is not None:
        total = phases['prep_time'] + phases['online_time']
        phases['prep_pct'] = round(100 * phases['prep_time'] / total, 1) if total > 0 else None
    result['phase_breakdown'] = phases

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text)
        print(f'wrote {args.out}')
    else:
        print(text)


if __name__ == '__main__':
    main()
