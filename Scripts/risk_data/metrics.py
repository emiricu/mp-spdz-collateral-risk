"""Pure calculations on price/volume data -- zero I/O, testable in
isolation. Everything here works in plain floats; policy.py is the only
place that scales results into the basis-point integers the .mpc program
reads.
"""

import math


def daily_returns(history: list) -> list:
    """history: list of {"date", "price_usd"}, oldest first.
    Returns simple daily returns (p[t]/p[t-1] - 1), one shorter."""
    prices = [h["price_usd"] for h in history]
    return [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]


def volatility(returns: list, annualize=True) -> float:
    """Sample standard deviation of daily returns, annualized by
    sqrt(365) by default (crypto trades every day, not just weekdays)."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    stdev = math.sqrt(variance)
    return stdev * math.sqrt(365) if annualize else stdev


def correlation(returns_a: list, returns_b: list) -> float:
    """Pearson correlation between two equal-length return series.
    Returns 0.0 for degenerate input (too short, or either series
    constant) rather than raising -- a flat series is a valid edge case
    for a freshly-listed or perfectly stable asset, not an error."""
    n = min(len(returns_a), len(returns_b))
    if n < 2:
        return 0.0
    a, b = returns_a[-n:], returns_b[-n:]
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((x - mean_b) ** 2 for x in b)
    denom = math.sqrt(var_a * var_b)
    return cov / denom if denom > 0 else 0.0


def liquidity_ratio(volume_24h_usd: float, collateral_value_usd: float) -> float:
    """24h volume as a multiple of the collateral's USD value -- how much
    of this position the market could plausibly absorb in a day. Returns
    0.0 for a zero/negative collateral value rather than raising: policy
    guard() is the place that rejects that as invalid input, this
    function just avoids dividing by zero along the way."""
    if collateral_value_usd <= 0:
        return 0.0
    return volume_24h_usd / collateral_value_usd
