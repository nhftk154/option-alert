"""Entrypoint for scan.yml. Full flow: market-hours gate -> load universe from
cache -> pick this run's shard -> read Sheet cooldown state -> scan equities +
metals + crypto (in parallel, see below) -> filter through cooldown -> send
survivors -> one batched write each to History and Cooldown.

Supports `--dry-run` (print instead of send/write) and `--tickers` (bypass
sharding, scan an explicit list) for manual workflow_dispatch testing.

Concurrency note: fetching each ticker/currency is independent network I/O,
so both the equity batch and the crypto batch are scanned with a thread pool
(data_deribit.py additionally parallelizes internally, since a single crypto
currency can involve hundreds of individual Deribit instrument-ticker calls).
Measured before parallelizing: ~10s/equity ticker, ~174s for one crypto
currency (Deribit) - sequential scanning of a realistic shard would blow past
any reasonable GitHub Actions job timeout. `cooldown_state` and
`history_buffer` are mutated from multiple worker threads, so every write to
them goes through `_alert_lock`.
"""

import argparse
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .alerts import build_alert_text_equity_volume, build_alert_text_options, send_alert_if_not_cooling
from .config import CONFIG
from .cooldown import flush_cooldown_state, load_cooldown_state
from .data_deribit import fetch_deribit_historical_volatility, get_deribit_option_chain
from .data_equity import fetch_option_chain, fetch_underlying_snapshot
from .equity_volume import check_equity_volume_anomaly
from .market_hours import is_nyse_open
from .models import AlertRecord
from .scoring import score_option_chain
from .sharding import get_shard_index, select_shard
from .sheets_client import append_history_rows, open_spreadsheet
from .universe import get_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EQUITY_CONCURRENCY = 8  # moderate - yfinance is an unofficial/fragile scraper
CRYPTO_CONCURRENCY = 2  # BTC + ETH; each already parallelizes internally


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tickers", default="", help="Comma-separated override, bypasses sharding")
    return parser.parse_args()


def _history_row(rec: AlertRecord) -> list:
    return [
        rec.timestamp_utc, rec.ticker, rec.asset_class, rec.kind, f"{rec.score:.1f}",
        f"{rec.sub_vol_oi:.1f}", f"{rec.sub_iv:.1f}", f"{rec.sub_block:.1f}",
        rec.strike, rec.expiry, f"{rec.notional_usd:.0f}",
    ]


def _record_alert_if_due(ticker, kind, text, rec, cooldown_state, alert_lock, now, dry_run, history_buffer):
    """Thread-safe: cooldown check + send + history append as one atomic step,
    so two worker threads can never both pass the cooldown check for the same
    key before either has recorded it."""
    with alert_lock:
        sent = send_alert_if_not_cooling(ticker, kind, text, cooldown_state, now, dry_run)
        if sent and rec is not None:
            history_buffer.append(_history_row(rec))
    return sent


def scan_equity_symbol(ticker, now, cooldown_state, alert_lock, dry_run, history_buffer):
    snapshot = fetch_underlying_snapshot(ticker)

    rows = fetch_option_chain(ticker)
    if rows:
        results = score_option_chain(rows, baseline_vol=snapshot.realized_vol_20d)
        for result in results:
            text = build_alert_text_options(result)
            rec = AlertRecord(
                timestamp_utc=now.isoformat(), ticker=result.ticker,
                asset_class=result.asset_class.value, kind=result.kind.value,
                score=result.score, sub_vol_oi=result.sub_vol_oi, sub_iv=result.sub_iv,
                sub_block=result.sub_block, strike=result.strike,
                expiry=result.expiry.isoformat(), notional_usd=result.notional_usd,
            )
            _record_alert_if_due(result.ticker, result.kind.value, text, rec, cooldown_state, alert_lock, now, dry_run, history_buffer)

    vol_alert = check_equity_volume_anomaly(snapshot)
    if vol_alert:
        text = build_alert_text_equity_volume(vol_alert)
        rec = AlertRecord(
            timestamp_utc=now.isoformat(), ticker=ticker, asset_class="EQUITY",
            kind="EQUITY_VOLUME", score=0, sub_vol_oi=0, sub_iv=0, sub_block=0,
            strike="", expiry="", notional_usd=0,
        )
        _record_alert_if_due(ticker, "EQUITY_VOLUME", text, rec, cooldown_state, alert_lock, now, dry_run, history_buffer)


def scan_crypto_symbol(currency, now, cooldown_state, alert_lock, dry_run, history_buffer):
    baseline_vol = fetch_deribit_historical_volatility(currency)
    rows = get_deribit_option_chain(currency)
    if not rows:
        return

    results = score_option_chain(rows, baseline_vol=baseline_vol)
    for result in results:
        text = build_alert_text_options(result)
        rec = AlertRecord(
            timestamp_utc=now.isoformat(), ticker=result.ticker,
            asset_class=result.asset_class.value, kind=result.kind.value,
            score=result.score, sub_vol_oi=result.sub_vol_oi, sub_iv=result.sub_iv,
            sub_block=result.sub_block, strike=result.strike,
            expiry=result.expiry.isoformat(), notional_usd=result.notional_usd,
        )
        _record_alert_if_due(result.ticker, result.kind.value, text, rec, cooldown_state, alert_lock, now, dry_run, history_buffer)


def _run_pool(fn, items, max_workers, *extra_args):
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fn, item, *extra_args): item for item in items}
        for future in futures:
            item = futures[future]
            try:
                future.result()
            except Exception as exc:
                logger.warning("scan failed for %s: %s", item, exc)


def main() -> int:
    args = parse_args()
    now = datetime.now(timezone.utc)

    override_tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    if not override_tickers and not is_nyse_open(now):
        logger.info("NYSE closed - nothing to do")
        return 0

    if override_tickers:
        # Manual smoke-test path: doesn't need the committed universe cache to
        # exist yet, only the fixed crypto list from config.
        equity_batch = override_tickers
        crypto_batch = list(CONFIG.universe.crypto)
    else:
        universe = get_universe()
        shard_index = get_shard_index(now, CONFIG.schedule.n_shards, CONFIG.schedule.shard_interval_minutes)
        shard = select_shard(universe.equities, shard_index, CONFIG.schedule.n_shards)
        equity_batch = shard + universe.metals
        crypto_batch = universe.crypto
        logger.info("shard %d/%d: %d equities + %d metals", shard_index, CONFIG.schedule.n_shards, len(shard), len(universe.metals))

    try:
        spreadsheet = open_spreadsheet()
        cooldown_state = load_cooldown_state(spreadsheet)
    except Exception as exc:
        logger.warning("could not open Sheets (%s) - proceeding with empty cooldown state", exc)
        spreadsheet = None
        cooldown_state = {}

    history_buffer: list[list] = []
    alert_lock = threading.Lock()

    # Equities (yfinance) and crypto (Deribit) hit different hosts, so run
    # both pools concurrently rather than one after the other.
    with ThreadPoolExecutor(max_workers=2) as top_pool:
        equity_future = top_pool.submit(_run_pool, scan_equity_symbol, equity_batch, EQUITY_CONCURRENCY, now, cooldown_state, alert_lock, args.dry_run, history_buffer)
        crypto_future = top_pool.submit(_run_pool, scan_crypto_symbol, crypto_batch, CRYPTO_CONCURRENCY, now, cooldown_state, alert_lock, args.dry_run, history_buffer)
        equity_future.result()
        crypto_future.result()

    logger.info("run complete: %d alerts sent", len(history_buffer))

    if spreadsheet is not None and not args.dry_run:
        append_history_rows(spreadsheet, history_buffer)
        flush_cooldown_state(spreadsheet, cooldown_state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
