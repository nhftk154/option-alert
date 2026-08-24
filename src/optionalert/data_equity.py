"""yfinance-backed data source for equities and metals ETFs (GLD/SLV).
Normalizes into the shared models.py schema so scoring.py is source-agnostic."""

import logging
import math
import random
import time
from datetime import date, datetime, timezone

import numpy as np

from .config import CONFIG
from .market_hours import NY_TZ
from .models import AssetClass, OptionContractRow, OptionKind, UnderlyingSnapshot

logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 2
_RETRY_BASE_SLEEP = 1.5


def _with_retry(fn, *args, **kwargs):
    last_exc = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # unofficial API - be defensive
            last_exc = exc
            time.sleep(_RETRY_BASE_SLEEP * (attempt + 1))
    raise last_exc


def _asset_class_for(ticker: str) -> AssetClass:
    if ticker in CONFIG.universe.metals:
        return AssetClass.METAL_ETF
    if ticker in CONFIG.universe.crypto_etfs:
        return AssetClass.CRYPTO_ETF
    return AssetClass.EQUITY


def fetch_option_chain(ticker: str, max_dte: int | None = None) -> list[OptionContractRow]:
    """All CALL/PUT rows across expirations within max_dte days, normalized."""
    import yfinance as yf

    max_dte = max_dte if max_dte is not None else CONFIG.thresholds.max_dte
    tk = yf.Ticker(ticker)
    underlying_price = _with_retry(lambda: tk.fast_info["last_price"])

    rows: list[OptionContractRow] = []
    today = date.today()
    today_ny = datetime.now(NY_TZ).date()
    expirations = _with_retry(lambda: tk.options)

    for expiry_str in expirations:
        expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        dte = (expiry - today).days
        if dte < 0 or dte > max_dte:
            continue

        chain = _with_retry(tk.option_chain, expiry_str)
        for kind, df in ((OptionKind.CALL, chain.calls), (OptionKind.PUT, chain.puts)):
            for _, r in df.iterrows():
                volume = r.get("volume")
                oi = r.get("openInterest")
                iv = r.get("impliedVolatility")
                if volume is None or math.isnan(volume) or not volume:
                    continue
                if iv is None or math.isnan(iv):
                    continue
                # yfinance doesn't zero out volume/iv for contracts that
                # simply haven't traded today - it keeps showing whatever was
                # last known, from however many days ago. lastTradeDate is
                # the only way to tell "traded today" from "stale snapshot".
                last_trade = r.get("lastTradeDate")
                if last_trade is None or last_trade != last_trade:  # NaT check, avoids a pandas import
                    continue
                if last_trade.tz_convert(NY_TZ).date() != today_ny:
                    continue
                oi = 0.0 if oi is None or math.isnan(oi) else oi
                rows.append(OptionContractRow(
                    ticker=ticker,
                    asset_class=_asset_class_for(ticker),
                    kind=kind,
                    strike=float(r["strike"]),
                    expiry=expiry,
                    dte=dte,
                    last_price=float(r.get("lastPrice") or 0),
                    volume=float(volume),
                    open_interest=float(oi),
                    iv=float(iv),
                    underlying_price=float(underlying_price),
                    contract_id=str(r.get("contractSymbol", "")),
                ))
        time.sleep(random.uniform(0.3, 0.8))  # gentle pacing between expirations

    return rows


def compute_realized_vol(prices) -> float:
    """Annualized realized volatility from a series of daily closes."""
    log_returns = np.diff(np.log(prices))
    if len(log_returns) < 2:
        return 0.0
    return float(np.std(log_returns, ddof=1) * math.sqrt(252))


def fetch_underlying_snapshot(ticker: str) -> UnderlyingSnapshot:
    import yfinance as yf

    lookback = CONFIG.thresholds.equity_volume_lookback_days
    tk = yf.Ticker(ticker)
    fi = _with_retry(lambda: tk.fast_info)
    hist = _with_retry(tk.history, period="45d")

    volumes = hist["Volume"].dropna()
    avg_volume_20d = float(volumes.tail(lookback + 1).iloc[:-1].mean()) if len(volumes) > lookback else float(volumes.mean())
    realized_vol = compute_realized_vol(hist["Close"].dropna().tail(lookback + 1).to_numpy())

    return UnderlyingSnapshot(
        ticker=ticker,
        last_price=float(fi["last_price"]),
        today_volume=float(fi.get("last_volume") or volumes.iloc[-1]),
        avg_volume_20d=avg_volume_20d,
        realized_vol_20d=realized_vol,
    )
