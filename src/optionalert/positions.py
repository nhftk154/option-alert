"""Large-position detection: every contract whose premium traded today
crosses a dollar threshold, bucketed into two tiers. Pure functions, no I/O.

"Position" here means the day's total premium in one contract (volume x 100
x last price) - there's no free trade-by-trade tape, so a single $5M block and
fifty $100K fills in the same contract look identical."""

from dataclasses import dataclass
from enum import Enum

from .config import CONFIG
from .models import OptionContractRow


class PositionTier(str, Enum):
    INFO = "INFO"  # position_info_usd <= premium < position_alert_usd
    ALERT = "ALERT"  # premium >= position_alert_usd


@dataclass
class PositionHit:
    row: OptionContractRow
    tier: PositionTier
    baseline_vol: float

    @property
    def premium_usd(self) -> float:
        return self.row.notional_usd

    @property
    def vol_oi_ratio(self) -> float:
        return self.row.volume / max(self.row.open_interest, 1)


def classify_premium(premium_usd: float) -> PositionTier | None:
    thresholds = CONFIG.thresholds
    if premium_usd >= thresholds.position_alert_usd:
        return PositionTier.ALERT
    if premium_usd >= thresholds.position_info_usd:
        return PositionTier.INFO
    return None


def find_large_positions(rows: list[OptionContractRow], baseline_vol: float) -> list[PositionHit]:
    """Every contract (not just the best per ticker) at or above the info
    threshold, within the DTE window, whose volume today is above
    position_min_vol_oi x its open interest - largest premium first."""
    thresholds = CONFIG.thresholds
    hits = []
    for row in rows:
        if row.dte < thresholds.min_dte or row.dte > thresholds.max_dte:
            continue
        tier = classify_premium(row.notional_usd)
        if tier is None:
            continue
        hit = PositionHit(row=row, tier=tier, baseline_vol=baseline_vol)
        if hit.vol_oi_ratio <= thresholds.position_min_vol_oi:
            continue
        hits.append(hit)
    hits.sort(key=lambda h: h.premium_usd, reverse=True)
    return hits
