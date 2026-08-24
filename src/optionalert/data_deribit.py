"""Deribit public REST API for BTC/ETH options. Fully free, no auth needed
for market data. Normalizes into the same models.py schema as data_equity.py."""

import logging
import time
from datetime import date, datetime, timezone

import requests

from .config import CONFIG
from .models import AssetClass, OptionContractRow, OptionKind

logger = logging.getLogger(__name__)

BASE_URL = "https://www.deribit.com/api/v2/public"
_TIMEOUT = 10


def _get(path: str, params: dict) -> dict:
    resp = requests.get(f"{BASE_URL}/{path}", params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    if "error" in payload and payload["error"]:
        raise RuntimeError(f"Deribit error for {path}: {payload['error']}")
    return payload["result"]


def fetch_deribit_instruments(currency: str, max_dte: int | None = None) -> list[str]:
    max_dte = max_dte if max_dte is not None else CONFIG.thresholds.max_dte
    instruments = _get("get_instruments", {"currency": currency, "kind": "option", "expired": "false"})

    today = date.today()
    names = []
    for inst in instruments:
        expiry = datetime.fromtimestamp(inst["expiration_timestamp"] / 1000, tz=timezone.utc).date()
        dte = (expiry - today).days
        if 0 <= dte <= max_dte:
            names.append(inst["instrument_name"])
    return names


def fetch_deribit_ticker(instrument_name: str) -> dict:
    return _get("ticker", {"instrument_name": instrument_name})


def fetch_deribit_historical_volatility(currency: str) -> float:
    """Trailing average of Deribit's own historical-volatility series, used as
    the IV baseline for crypto (equivalent role to realized_vol for equities)."""
    data = _get("get_historical_volatility", {"currency": currency})
    if not data:
        return 0.0
    values = [point[1] for point in data]
    return float(sum(values) / len(values)) / 100.0  # Deribit returns a percentage number


def _parse_instrument_name(name: str) -> tuple[date, float, OptionKind]:
    # Format: BTC-27SEP24-65000-C
    _, expiry_str, strike_str, kind_char = name.split("-")
    expiry = datetime.strptime(expiry_str, "%d%b%y").date()
    kind = OptionKind.CALL if kind_char == "C" else OptionKind.PUT
    return expiry, float(strike_str), kind


def get_deribit_option_chain(currency: str, max_dte: int | None = None) -> list[OptionContractRow]:
    max_dte = max_dte if max_dte is not None else CONFIG.thresholds.max_dte
    baseline_vol = fetch_deribit_historical_volatility(currency)
    today = date.today()

    rows: list[OptionContractRow] = []
    for name in fetch_deribit_instruments(currency, max_dte):
        try:
            t = fetch_deribit_ticker(name)
        except Exception as exc:
            logger.warning("deribit ticker fetch failed for %s: %s", name, exc)
            continue

        volume = t.get("stats", {}).get("volume") or 0
        oi = t.get("open_interest") or 0
        iv = t.get("mark_iv")
        index_price = t.get("index_price")
        if not volume or iv is None or not index_price:
            continue

        expiry, strike, kind = _parse_instrument_name(name)
        dte = (expiry - today).days

        rows.append(OptionContractRow(
            ticker=currency,
            asset_class=AssetClass.CRYPTO,
            kind=kind,
            strike=strike,
            expiry=expiry,
            dte=dte,
            last_price=float(t.get("last_price") or 0),
            volume=float(volume),
            open_interest=float(oi),
            iv=float(iv) / 100.0,  # Deribit mark_iv is a percentage number
            underlying_price=float(index_price),
            contract_id=name,
        ))
        time.sleep(0.05)

    # Attach the shared baseline via a side channel: caller (run_scan.py) passes
    # baseline_vol separately into scoring since OptionContractRow has no field
    # for it (baseline is per-underlying, not per-contract).
    return rows
