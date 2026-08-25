"""Local Black-Scholes implied-vol solve, pure stdlib (statistics.NormalDist
for the normal CDF - no scipy/py_vollib dependency).

Exists so IV correction doesn't depend on tvremix/TVREMIX_API_KEY being
configured: given a fresh bid/ask mid-quote and the underlying price already
fetched in the same scan cycle (both from yfinance, see data_equity.py),
this derives IV ourselves instead of trusting yfinance's own often-stale
`impliedVolatility` field. r=0, q=0 (no risk-free-rate/dividend config
exists in this repo) is an acceptable approximation for the short-DTE range
this scanner targets - this feeds a relative anomaly score, not a pricing
system, and the tickers scanned are typically non-dividend-paying or
dividend effects are negligible over the DTE window.
"""

import math
from statistics import NormalDist

from .models import OptionKind

_N = NormalDist()
_MIN_SIGMA = 0.01
_MAX_SIGMA = 5.0
_TOLERANCE = 1e-6
_MAX_ITER = 100
_MIN_TIME_VALUE_RATIO = 0.05


def _bs_price(S: float, K: float, T: float, sigma: float, kind: OptionKind, r: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        intrinsic = max(0.0, S - K) if kind == OptionKind.CALL else max(0.0, K - S)
        return intrinsic

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if kind == OptionKind.CALL:
        return S * _N.cdf(d1) - K * math.exp(-r * T) * _N.cdf(d2)
    return K * math.exp(-r * T) * _N.cdf(-d2) - S * _N.cdf(-d1)


def implied_vol(
    price: float, S: float, K: float, dte: int, kind: OptionKind, r: float = 0.0,
) -> float | None:
    """Bisection search for sigma (chosen over Newton-Raphson: robust even
    when vega is tiny, which happens at very short DTE - exactly the range
    this scanner cares about). Returns None if `price` is outside
    no-arbitrage bounds for any sigma in [_MIN_SIGMA, _MAX_SIGMA], or if S/K
    aren't positive.

    dte: whole days to expiration - converted to a year-fraction (T = dte/365).

    Also returns None when time value (price - intrinsic) is a thin sliver of
    price (< _MIN_TIME_VALUE_RATIO). Deep ITM contracts near expiry have
    near-zero vega under this model, so the price-to-vol mapping is nearly
    flat there: a fraction of a cent of bid/ask noise swings the solved
    sigma wildly, and a plain European solve also misses the early-exercise
    premium real (American-style) puts carry - the same regime where
    yfinance's own IV is least trustworthy. A confidently-precise-looking
    number here would be worse than admitting we don't know; callers should
    fall back to a real corroborating source (tvremix) or flag it unverified.
    """
    if price <= 0 or S <= 0 or K <= 0 or dte <= 0:
        return None

    intrinsic = max(0.0, S - K) if kind == OptionKind.CALL else max(0.0, K - S)
    if price < intrinsic:
        return None  # below no-arbitrage floor - not a valid option price
    if (price - intrinsic) < price * _MIN_TIME_VALUE_RATIO:
        return None  # too close to intrinsic - vol estimate would be unreliable

    T = dte / 365.0

    lo, hi = _MIN_SIGMA, _MAX_SIGMA
    price_lo = _bs_price(S, K, T, lo, kind, r) - price
    price_hi = _bs_price(S, K, T, hi, kind, r) - price
    if price_lo > 0 or price_hi < 0:
        return None  # price outside what's reachable in [_MIN_SIGMA, _MAX_SIGMA]

    for _ in range(_MAX_ITER):
        mid = (lo + hi) / 2
        diff = _bs_price(S, K, T, mid, kind, r) - price
        if abs(diff) < _TOLERANCE:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid

    return (lo + hi) / 2
