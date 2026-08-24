"""Shared data shapes that data_equity.py normalizes every ticker (equity,
metal ETF, or crypto-linked ETF) into, so scoring.py never needs to know
which kind of ticker a row came from."""

from dataclasses import dataclass
from datetime import date
from enum import Enum


class OptionKind(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    METAL_ETF = "METAL_ETF"
    CRYPTO_ETF = "CRYPTO_ETF"


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
        # Dollar value of premium traded (the block/sweep-size proxy), not the
        # notional value of shares/coins controlled. 1 contract = 100 shares,
        # priced at the option premium - true for equities and every ETF
        # (metal or crypto-linked) alike, since they're all standard
        # exchange-listed options quoted in USD.
        return self.volume * 100 * self.last_price


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
    dte: int
    volume: float
    open_interest: float
    last_price: float
    underlying_price: float


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
