"""Builds and caches the tracked ticker universe: top-N S&P 500 names by
market cap, plus the fixed metals/crypto symbols. scan.yml only ever reads
the committed cache (get_universe) — it never rebuilds it live."""

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import CONFIG

logger = logging.getLogger(__name__)

WIKI_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


@dataclass
class UniverseData:
    equities: list  # market-cap sorted, largest first
    metals: list
    crypto: list
    generated_at_utc: str
    tvremix_symbols: dict = None  # ticker -> "EXCHANGE:TICKER"; empty/missing entries just mean run_scan.py skips tvremix refinement for that ticker


def fetch_sp500_symbols() -> list[str]:
    """Scrape the current S&P 500 constituent list from Wikipedia (free, no key).

    Wikipedia's edge (Wikimedia) returns 403 Forbidden to requests with no
    User-Agent header, which is exactly what pandas.read_html sends by
    default - so the page is fetched manually with a browser-like header
    first, then handed to read_html as raw HTML."""
    import io

    import pandas as pd
    import requests

    headers = {"User-Agent": "Mozilla/5.0 (compatible; option-alert/1.0)"}
    resp = requests.get(WIKI_SP500_URL, headers=headers, timeout=15)
    resp.raise_for_status()

    tables = pd.read_html(io.StringIO(resp.text))
    constituents = tables[0]
    symbols = constituents["Symbol"].astype(str).str.replace(".", "-", regex=False)
    return symbols.tolist()


_MARKET_CAP_WORKERS = 10


def _fetch_market_cap(sym: str) -> tuple[str, float] | None:
    import yfinance as yf

    fi = yf.Ticker(sym).fast_info
    cap = fi.get("market_cap") or fi.get("marketCap")
    return (sym, float(cap)) if cap else None


def rank_by_market_cap(symbols: list[str], top_n: int) -> list[str]:
    """Rank symbols by market cap (yfinance fast_info, cheaper than .info) and
    keep the top N. Tickers that fail are skipped, not fatal.

    Bootstrapping the real 503-symbol S&P list sequentially took 14m36s, with
    a single ticker (MRK) hanging for over 5 minutes on yfinance's own
    internal HTTP timeout - nearly exhausting refresh_universe.yml's job
    budget on its own, since a sequential loop means every other ticker waits
    behind that one stuck request. Fetches now run concurrently (10 workers),
    so a stuck ticker no longer blocks the other ~500 from completing almost
    immediately - but note this does NOT put a hard cap on any single
    ticker's own worst case: concurrent.futures gives no way to abandon an
    already-running thread, so the function still can't return until every
    submitted fetch finishes (or hits yfinance's own internal timeout,
    observed at ~5 min). refresh_universe.yml's timeout-minutes has a wide
    margin above that for this reason."""
    import concurrent.futures

    ranked = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MARKET_CAP_WORKERS) as pool:
        futures = {pool.submit(_fetch_market_cap, sym): sym for sym in symbols}
        for future in concurrent.futures.as_completed(futures):
            sym = futures[future]
            try:
                result = future.result()
                if result:
                    ranked.append(result)
            except Exception as exc:
                logger.warning("market cap lookup failed for %s: %s", sym, exc)

    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return [sym for sym, _ in ranked[:top_n]]


_TVREMIX_PACE_SECONDS = 3.5  # tvremix's own limit is 20/min; this keeps a margin


def resolve_tvremix_symbols(tickers: list[str]) -> dict:
    """Best-effort ticker -> 'EXCHANGE:TICKER' resolution for tvremix, paced
    to stay under its rate limit. No-ops immediately (no network calls) if
    TVREMIX_API_KEY isn't set - resolve_symbol() returns None for every
    ticker right away in that case. Only runs in the weekly refresh job, not
    the frequent scan job, precisely because this is slow at 300 tickers."""
    import time

    from . import tvremix_client

    if not os.environ.get("TVREMIX_API_KEY"):
        return {}

    resolved = {}
    for i, ticker in enumerate(tickers):
        symbol = tvremix_client.resolve_symbol(ticker)
        if symbol:
            resolved[ticker] = symbol
        if i < len(tickers) - 1:
            time.sleep(_TVREMIX_PACE_SECONDS)
    return resolved


def build_universe(top_n: int | None = None) -> UniverseData:
    top_n = top_n or CONFIG.universe.sp_top_n
    symbols = fetch_sp500_symbols()
    top = rank_by_market_cap(symbols, top_n)
    metals = list(CONFIG.universe.metals)
    tvremix_symbols = resolve_tvremix_symbols(top + metals)
    return UniverseData(
        equities=top,
        metals=metals,
        crypto=list(CONFIG.universe.crypto),
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        tvremix_symbols=tvremix_symbols,
    )


def save_universe_cache(universe: UniverseData, path: str | None = None) -> None:
    path = path or CONFIG.universe.cache_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(asdict(universe), indent=2, ensure_ascii=False))


def load_universe_cache(path: str | None = None) -> UniverseData:
    path = path or CONFIG.universe.cache_path
    raw = json.loads(Path(path).read_text())
    return UniverseData(**raw)


def get_universe(path: str | None = None) -> UniverseData:
    """Read-only accessor for run_scan.py. Warns (but does not crash) if the
    cache is stale — a broken weekly refresh job should degrade, not take
    down the whole alert pipeline."""
    path = path or CONFIG.universe.cache_path
    universe = load_universe_cache(path)

    generated = datetime.fromisoformat(universe.generated_at_utc)
    age_days = (datetime.now(timezone.utc) - generated).days
    if age_days > CONFIG.universe.max_cache_age_days:
        logger.warning(
            "universe cache is %d days old (max %d) - refresh_universe.yml may be broken",
            age_days, CONFIG.universe.max_cache_age_days,
        )
    return universe
