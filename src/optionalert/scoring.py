"""Composite 0-100 "how unusual is this" score. Pure functions, no I/O -
easy to unit test with synthetic OptionContractRow fixtures."""

import math

from .config import CONFIG
from .models import OptionContractRow, ScoreResult


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def score_contract(row: OptionContractRow, baseline_vol: float) -> ScoreResult | None:
    thresholds = CONFIG.thresholds
    weights = CONFIG.scoring_weights

    notional = row.notional_usd
    if notional < thresholds.min_notional_usd:
        return None
    if row.dte < 0 or row.dte > thresholds.max_dte:
        return None

    vol_oi_ratio = row.volume / max(row.open_interest, 1)
    sub_vol_oi = _clamp(vol_oi_ratio / thresholds.vol_oi_cap_ratio * 100)

    iv_spike_ratio = max(0.0, (row.iv - baseline_vol)) / max(baseline_vol, 1e-6)
    sub_iv = _clamp(iv_spike_ratio / thresholds.iv_spike_cap_ratio * 100)

    floor = thresholds.block_notional_floor_usd
    cap = thresholds.block_notional_cap_usd
    if notional <= floor:
        sub_block = 0.0
    elif notional >= cap:
        sub_block = 100.0
    else:
        sub_block = _clamp((math.log(notional) - math.log(floor)) / (math.log(cap) - math.log(floor)) * 100)

    composite = weights.vol_oi * sub_vol_oi + weights.iv_spike * sub_iv + weights.block_sweep * sub_block

    return ScoreResult(
        ticker=row.ticker,
        asset_class=row.asset_class,
        kind=row.kind,
        score=composite,
        sub_vol_oi=sub_vol_oi,
        sub_iv=sub_iv,
        sub_block=sub_block,
        strike=row.strike,
        expiry=row.expiry,
        notional_usd=notional,
        vol_oi_ratio=vol_oi_ratio,
        iv=row.iv,
        baseline_vol=baseline_vol,
    )


def score_option_chain(rows: list[OptionContractRow], baseline_vol: float) -> list[ScoreResult]:
    """Score every row, keep only the single highest-scoring contract per
    (ticker, CALL/PUT), and filter to the alert threshold."""
    threshold = CONFIG.thresholds.alert_score_threshold
    best: dict[tuple, ScoreResult] = {}

    for row in rows:
        result = score_contract(row, baseline_vol)
        if result is None:
            continue
        key = (result.ticker, result.kind)
        if key not in best or result.score > best[key].score:
            best[key] = result

    return [r for r in best.values() if r.score >= threshold]
