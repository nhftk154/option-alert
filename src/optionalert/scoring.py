"""Composite 0-100 "how unusual is this" score. Pure functions, no I/O -
easy to unit test with synthetic OptionContractRow fixtures."""

import dataclasses
import math

from .config import CONFIG
from .models import OptionContractRow, ScoreResult


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _sub_iv(iv: float, baseline_vol: float) -> float:
    thresholds = CONFIG.thresholds
    iv_spike_ratio = max(0.0, (iv - baseline_vol)) / max(baseline_vol, 1e-6)
    return _clamp(iv_spike_ratio / thresholds.iv_spike_cap_ratio * 100)


def score_contract(row: OptionContractRow, baseline_vol: float) -> ScoreResult | None:
    thresholds = CONFIG.thresholds
    weights = CONFIG.scoring_weights

    notional = row.notional_usd
    if notional < thresholds.min_notional_usd:
        return None
    if row.dte < thresholds.min_dte or row.dte > thresholds.max_dte:
        return None
    if row.open_interest < thresholds.min_open_interest:
        return None

    vol_oi_ratio = row.volume / max(row.open_interest, 1)
    sub_vol_oi = _clamp(vol_oi_ratio / thresholds.vol_oi_cap_ratio * 100)

    sub_iv = _sub_iv(row.iv, baseline_vol)

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
        dte=row.dte,
        volume=row.volume,
        open_interest=row.open_interest,
        last_price=row.last_price,
        underlying_price=row.underlying_price,
    )


def rescore_with_iv(result: ScoreResult, new_iv: float) -> ScoreResult:
    """Recomputes the composite score using a corroborating IV (from
    tvremix) in place of the original - only sub_iv and the composite
    change, since vol/oi and block-size don't depend on IV at all."""
    weights = CONFIG.scoring_weights
    sub_iv = _sub_iv(new_iv, result.baseline_vol)
    composite = weights.vol_oi * result.sub_vol_oi + weights.iv_spike * sub_iv + weights.block_sweep * result.sub_block
    return dataclasses.replace(result, iv=new_iv, sub_iv=sub_iv, score=composite)


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
