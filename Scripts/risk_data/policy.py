"""Scaling and validation for values crossing into MP-SPDZ, plus the one
calculation (liquidation price) that only makes sense to do AFTER the MPC
reveal, on the now-public exposure figure.

BPS and POLICY_VERSION here are NOT the source of truth -- the .mpc
program's own compile-time constants are (Programs/Source/
crypto_collateral_risk.mpc, "Policy constants" section). They are
duplicated here only so this module can scale numbers the same way
before they enter MPC as input; bump both together if either changes.
"""

BPS = 10000
POLICY_VERSION = 1


class PolicyError(ValueError):
    pass


def to_bps(fraction: float) -> int:
    """0.0653 (6.53%) -> 653 basis points."""
    return int(round(fraction * BPS))


def to_usd_int(amount: float) -> int:
    """Whole-dollar USD, no cents -- collateralized loans in this demo's
    scope are well above the $1 threshold where rounding matters."""
    return int(round(amount))


def guard(name: str, value: int, lo: int, hi: int) -> int:
    """Range-check a value BEFORE it is written to an Input-P<i>-0 file.
    This is the only place these numbers are still in the clear, so it is
    the only place a bad value (bug upstream, API returning nonsense) can
    be caught with a readable error -- past this point it becomes a
    secret share, and an out-of-range value would silently wrap around
    the ring/binary domain instead of raising, corrupting the decision
    with no visible error."""
    if not (lo <= value <= hi):
        raise PolicyError(f"{name}={value} out of allowed range [{lo}, {hi}]")
    return value


def liquidation_price(collateral_amount: float, exposure_usd: int,
                       liq_threshold_bps: int) -> float:
    """Collateral price at which exposure/collateral_value crosses the
    (now-revealed) liquidation threshold. Deliberately computed here, in
    the clear, AFTER the MPC reveal -- it depends on collateral_amount
    (a quantity, not a value, private to the requester and never sent
    into MPC in that form) and on liq_threshold_bps, which is public only
    once revealed."""
    if collateral_amount <= 0 or liq_threshold_bps <= 0:
        raise PolicyError("collateral_amount and liq_threshold_bps must be positive")
    collateral_value_at_liq = exposure_usd * BPS / liq_threshold_bps
    return collateral_value_at_liq / collateral_amount


# Upper bound on exposure implied by each leverage tier from
# crypto_collateral_risk.mpc's leverage_tier_and_score(), as a fraction of
# budget = collateral_value_usd * max_ltv_bps -- must stay in sync with
# that function's half/three_q/within cutoffs (exposure*BPS*2<=budget,
# exposure*BPS*4<=budget*3, exposure*BPS<=budget respectively). Tier 3
# ("over policy") has no upper bound by construction, so it is
# deliberately absent here -- see liquidation_price_usd's docstring.
TIER_UPPER_BOUND_OF_BUDGET = {0: 0.5, 1: 0.75, 2: 1.0}


def liquidation_price_usd(collateral_amount: float, max_ltv_bps: int,
                           collateral_value_usd: int, exposure_usd: int = None,
                           liq_threshold_bps: int = None, lev_tier: int = None):
    """N-aware liquidation price. Returns (price_or_None, note_or_None).

    N>=3 (exact): pass exposure_usd -- revealed exactly at N>=3 in both
    MODE_BENCHMARK and MODE_PRODUCTION (see the .mpc's module docstring).
    liq_threshold_bps is also revealed in MODE_BENCHMARK; pass it for the
    exact answer. If omitted (MODE_PRODUCTION never reveals it), falls
    back to max_ltv_bps as the threshold -- still exact on exposure, just
    using a stricter-than-true threshold, so still safely conservative.

    N==2 (conservative): pass lev_tier instead of exposure_usd -- the
    only exposure signal ever revealed at N==2, by design (the .mpc
    never reveals the exact aggregate there, to anyone, regardless of
    mode -- see its module docstring on the subtraction attack this
    prevents). Estimated using the tier's upper-bound exposure (worst
    case within the band). Threshold is liq_threshold_bps if available
    (MODE_BENCHMARK) else max_ltv_bps (MODE_PRODUCTION, which never
    reveals liq_threshold_bps at all).

    Both approximations -- tier upper bound instead of exact exposure,
    max_ltv_bps instead of the true (always >=) liq_threshold_bps -- only
    ever push the computed price UP: less collateral value is needed to
    trigger the approximated threshold than the true one, so the
    estimate signals liquidation risk at a higher (nearer-to-spot) price
    than reality, i.e. earlier as price falls toward it, never later.

    Tier 3 has no upper bound on exposure by construction: returns
    (None, note) rather than a fabricated number -- esito is already
    RIFIUTATO/over-policy at that tier, which is the operative signal,
    not a numeric price.
    """
    threshold = liq_threshold_bps if liq_threshold_bps is not None else max_ltv_bps

    if exposure_usd is not None:
        return liquidation_price(collateral_amount, exposure_usd, threshold), None

    if lev_tier is None:
        raise PolicyError(
            'liquidation_price_usd needs either exposure_usd (N>=3, exact) '
            'or lev_tier (N==2, conservative)')
    if lev_tier not in TIER_UPPER_BOUND_OF_BUDGET:
        return None, (
            'leverage tier 3 ("over policy") has no upper bound on exposure '
            '-- no finite conservative liquidation price can be given; '
            'esito is already RIFIUTATO/over-policy, treat that as the '
            'operative signal instead of a numeric price')

    budget = collateral_value_usd * max_ltv_bps
    conservative_exposure_usd = TIER_UPPER_BOUND_OF_BUDGET[lev_tier] * budget / BPS
    return liquidation_price(collateral_amount, conservative_exposure_usd, threshold), None
