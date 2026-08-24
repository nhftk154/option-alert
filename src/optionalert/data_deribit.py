"""Deribit public REST API for BTC/ETH options. Fully free, no auth needed
for market data. Normalizes into the same models.py schema as data_equity.py."""

import concurrent.futures
import logging
import random
import time
from datetime import date, datetime, timezone

import requests

from .config import CONFIG
from .models import AssetClass, OptionContractRow, OptionKind

logger = logging.getLogger(__name__)

BASE_URL = "https://www.deribit.com/api/v2/public"
_TIMEOUT = 10
_MAX_RETRIES = 5


def _get(path: str, params: dict) -> dict:
    """Retries on HTTP 429 with exponential backoff + jitter. Measured: firing
    20 concurrent ticker requests at once reliably triggers Deribit's rate
    limit, and those requests were previously just dropped (logged + skipped)
    rather than retried - silently thinning out the crypto option chain."""
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        resp = requests.get(f"{BASE_URL}/{path}", params=params, timeout=_TIMEOUT)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            sleep_s = float(retry_after) if retry_after else (0.5 * (2 ** attempt) + random.uniform(0, 0.3))
            time.sleep(min(sleep_s, 10))
            last_exc = RuntimeError(f"429 rate-limited on {path} (attempt {attempt + 1}/{_MAX_RETRIES})")
            continue
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload and payload["error"]:
            raise RuntimeError(f"Deribit error for {path}: {payload['error']}")
        return payload["result"]

    raise last_exc


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
    instrument_names = fetch_deribit_instruments(currency, max_dte)

    # Measured: fetching ~260 instrument tickers sequentially took ~174s (the
    # dominant cost in a scan run, far more than the equity side). Deribit is
    # a real REST API, so this is worth parallelizing - but measured: 20
    # concurrent workers reliably triggers Deribit's per-IP rate limit (HTTP
    # 429) within the first second. 8 workers plus _get()'s retry/backoff
    # keeps requests under the limit without losing rows to silently-dropped
    # 429s.
    def _fetch_one(name: str) -> dict | None:
        try:
            return fetch_deribit_ticker(name)
        except Exception as exc:
            logger.warning("deribit ticker fetch failed for %s: %s", name, exc)
            return None

    tickers_by_name: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for name, ticker in zip(instrument_names, pool.map(_fetch_one, instrument_names)):
            if ticker is not None:
                tickers_by_name[name] = ticker

    rows: list[OptionContractRow] = []
    for name, t in tickers_by_name.items():
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

    # The baseline (`baseline_vol`, computed above) is passed separately into
    # scoring.py since OptionContractRow has no field for it - the baseline is
    # per-underlying, not per-contract.
    return rows
