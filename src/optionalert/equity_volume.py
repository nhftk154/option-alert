"""Equity-side anomaly check: today's stock volume vs its own 20-day average.
Independent of the options-scoring logic, same ticker universe."""

from .config import CONFIG
from .models import EquityVolumeAlert, UnderlyingSnapshot


def check_equity_volume_anomaly(snapshot: UnderlyingSnapshot) -> EquityVolumeAlert | None:
    threshold = CONFIG.thresholds.equity_volume_multiplier
    if snapshot.avg_volume_20d <= 0:
        return None

    ratio = snapshot.today_volume / snapshot.avg_volume_20d
    if ratio < threshold:
        return None

    return EquityVolumeAlert(
        ticker=snapshot.ticker,
        today_volume=snapshot.today_volume,
        avg_volume_20d=snapshot.avg_volume_20d,
        ratio=ratio,
    )
