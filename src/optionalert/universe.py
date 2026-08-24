"""Builds and caches the tracked ticker universe: top-N S&P 500 names by
market cap, plus the fixed metals/crypto symbols. scan.yml only ever reads
the committed cache (get_universe) — it never rebuilds it live."""

import json
import logging
import time
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


def rank_by_market_cap(symbols: list[str], top_n: int) -> list[str]:
    """Rank symbols by market cap (yfinance fast_info, cheaper than .info) and
    keep the top N. Tickers that fail to fetch are skipped, not fatal."""
    import yfinance as yf

    ranked = []
    for sym in symbols:
        try:
            fi = yf.Ticker(sym).fast_info
            cap = fi.get("market_cap") or fi.get("marketCap")
            if cap:
                ranked.append((sym, float(cap)))
        except Exception as exc:
            logger.warning("market cap lookup failed for %s: %s", sym, exc)
        time.sleep(0.1)  # gentle pacing against the unofficial Yahoo endpoint

    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return [sym for sym, _ in ranked[:top_n]]


def build_universe(top_n: int | None = None) -> UniverseData:
    top_n = top_n or CONFIG.universe.sp_top_n
    symbols = fetch_sp500_symbols()
    top = rank_by_market_cap(symbols, top_n)
    return UniverseData(
        equities=top,
        metals=list(CONFIG.universe.metals),
        crypto=list(CONFIG.universe.crypto),
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
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
