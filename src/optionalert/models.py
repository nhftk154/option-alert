"""Shared data shapes. data_equity.py and data_deribit.py both normalize into
these so scoring.py never needs to know which source a row came from."""

from dataclasses import dataclass
from datetime import date
from enum import Enum


class OptionKind(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    METAL_ETF = "METAL_ETF"
    CRYPTO = "CRYPTO"


@dataclass
class OptionContractRow:
    ticker: str
    asset_class: AssetClass
    kind: OptionKind
    strike: float
    expiry: date
    dte: int
    last_price: float
    volume: float
    open_interest: float
    iv: float  # implied volatility, as a decimal fraction e.g. 0.45 = 45%
    underlying_price: float
    contract_id: str

    @property
    def notional_usd(self) -> float:
        # Equities/ETFs: 1 contract = 100 shares. Deribit rows already fold this
        # into `volume` (contracts are 1 unit of the coin), so this is scaled by
        # the caller via a `contract_multiplier` when needed — see data_*.py.
        return self.volume * self.underlying_price


@dataclass
class UnderlyingSnapshot:
    ticker: str
    last_price: float
    today_volume: float
    avg_volume_20d: float
    realized_vol_20d: float  # annualized, decimal fraction


@dataclass
class ScoreResult:
    ticker: str
    asset_class: AssetClass
    kind: OptionKind
    score: float
    sub_vol_oi: float
    sub_iv: float
    sub_block: float
    strike: float
    expiry: date
    notional_usd: float
    vol_oi_ratio: float
    iv: float
    baseline_vol: float


@dataclass
class EquityVolumeAlert:
    ticker: str
    today_volume: float
    avg_volume_20d: float
    ratio: float


@dataclass
class AlertRecord:
    timestamp_utc: str
    ticker: str
    asset_class: str
    kind: str
    score: float
    sub_vol_oi: float
    sub_iv: float
    sub_block: float
    strike: "float | str"
    expiry: "str"
    notional_usd: float
