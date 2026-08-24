"""Central configuration. Every threshold/weight/schedule constant lives here —
no other module should hardcode a number that a user might want to retune."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoringWeights:
    vol_oi: float = 0.40
    iv_spike: float = 0.35
    block_sweep: float = 0.25


@dataclass(frozen=True)
class Thresholds:
    min_notional_usd: float = 100_000
    max_dte: int = 45
    # Score (0-100) above which a Telegram alert fires. Start at 70; the plan is
    # to retune this after ~1 week of live data once the History sheet has a
    # score distribution to look at.
    alert_score_threshold: float = 70
    # Score above which the alert gets the "extreme" red emoji instead of yellow.
    severity_extreme: float = 85
    # Volume/OpenInterest ratio that saturates the vol/oi subscore to 100.
    vol_oi_cap_ratio: float = 5.0
    # (IV - baseline_vol) / baseline_vol ratio that saturates the IV subscore to 100.
    iv_spike_cap_ratio: float = 1.0
    # Log-scale floor/cap (USD notional) for the block/sweep-size subscore.
    block_notional_floor_usd: float = 100_000
    block_notional_cap_usd: float = 1_000_000
    # Stock (equity) volume vs its own 20-day average that counts as "unusual".
    equity_volume_multiplier: float = 3.0
    equity_volume_lookback_days: int = 20
    # Minimum minutes between two alerts for the same (ticker, call/put or "EQUITY").
    cooldown_minutes: int = 30


@dataclass(frozen=True)
class ScheduleConfig:
    # n_shards=6 -> 50 equities/shard, full 300-name coverage every hour.
    # Relies on run_scan.py scanning each shard with 8-way concurrency
    # (EQUITY_CONCURRENCY) - sequential scanning measured ~10s/ticker, which
    # would make a 50-ticker shard alone take ~8 minutes. Parallelized, 10
    # equities + BTC + ETH (crypto also parallelized, see data_deribit.py)
    # measured 52s end-to-end; see the timeout-minutes comment in
    # .github/workflows/scan.yml for the full extrapolation to production size.
    n_shards: int = 6
    shard_interval_minutes: int = 10


@dataclass(frozen=True)
class UniverseConfig:
    sp_top_n: int = 300
    metals: tuple = ("GLD", "SLV")
    crypto: tuple = ("BTC", "ETH")
    cache_path: str = "data/universe_cache.json"
    max_cache_age_days: int = 21


@dataclass(frozen=True)
class SheetsConfig:
    history_tab: str = "History"
    cooldown_tab: str = "Cooldown"
    history_header: tuple = (
        "timestamp_utc", "ticker", "asset_class", "kind", "score",
        "sub_vol_oi", "sub_iv", "sub_block", "strike", "expiry", "notional_usd",
    )
    cooldown_header: tuple = ("ticker", "kind", "last_alert_utc")


@dataclass(frozen=True)
class AppConfig:
    scoring_weights: ScoringWeights = field(default_factory=ScoringWeights)
    thresholds: Thresholds = field(default_factory=Thresholds)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    sheets: SheetsConfig = field(default_factory=SheetsConfig)


CONFIG = AppConfig()
