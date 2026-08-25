"""Entrypoint for scan.yml. Full flow: market-hours gate -> load universe from
cache -> pick this run's shard -> read Sheet cooldown state -> scan equities +
metal/crypto ETFs (one thread pool, see below) -> filter through cooldown ->
send survivors -> one batched write each to History and Cooldown.

Supports `--dry-run` (print instead of send/write) and `--tickers` (bypass
sharding, scan an explicit list) for manual workflow_dispatch testing.

Concurrency note: fetching each ticker is independent network I/O, so the
whole batch (equities + metal ETFs + crypto-linked ETFs - all plain yfinance
tickers, see universe.py) is scanned with one thread pool. Measured: ~10s/
ticker sequentially, which would make a 50-ticker shard alone take ~8
minutes - parallelized to 8 workers instead. `cooldown_state` and
`history_buffer` are mutated from multiple worker threads, so every write to
them goes through `_alert_lock`.
"""

import argparse
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import tvremix_client
from .alerts import build_alert_text_equity_volume, build_alert_text_options, send_alert_if_not_cooling
from .config import CONFIG
from .cooldown import flush_cooldown_state, load_cooldown_state
from .data_equity import fetch_option_chain, fetch_underlying_snapshot
from .equity_volume import check_equity_volume_anomaly
from .market_hours import is_nyse_open
from .models import AlertRecord
from .scoring import rescore_with_iv, score_option_chain
from .sharding import get_shard_index, select_shard
from .sheets_client import append_history_rows, open_spreadsheet
from .universe import get_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EQUITY_CONCURRENCY = 8  # moderate - yfinance is an unofficial/fragile scraper


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


def _refine_with_tvremix(result, tvremix_symbol):
    """Corroborates the IV leg with tvremix (more reliable than yfinance's,
    see README) for the one contract that's about to alert. Best-effort:
    returns the original result unchanged on any miss - tvremix doesn't
    carry volume/OI at all, so it can never be a hard dependency here."""
    if not tvremix_symbol:
        return result
    new_iv = tvremix_client.refine_iv(
        tvremix_symbol, result.expiry.isoformat(), result.strike, result.kind.value.lower(),
    )
    if new_iv is None:
        logger.info("tvremix: no corroborating IV for %s %s %s", result.ticker, result.strike, result.kind.value)
        return result
    rescored = rescore_with_iv(result, new_iv)
    logger.info(
        "tvremix: refined %s %s %s IV %.0f%% -> %.0f%%, score %.0f -> %.0f",
        result.ticker, result.strike, result.kind.value,
        result.iv * 100, new_iv * 100, result.score, rescored.score,
    )
    return rescored


def scan_equity_symbol(ticker, now, cooldown_state, alert_lock, dry_run, history_buffer, tvremix_symbols):
    snapshot = fetch_underlying_snapshot(ticker)

    rows = fetch_option_chain(ticker)
    if rows:
        results = score_option_chain(rows, baseline_vol=snapshot.realized_vol_20d)
        for result in results:
            result = _refine_with_tvremix(result, tvremix_symbols.get(ticker))
            if result.score < CONFIG.thresholds.alert_score_threshold:
                continue  # tvremix's IV pulled it back under the bar - skip, don't send
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
        # exist yet, but still always covers metals/crypto ETFs too (cheap -
        # they're plain tickers now, no separate slow pipeline), and tries to
        # load tvremix_symbols from the cache if it does exist, purely so
        # manual --tickers test runs can also exercise tvremix refinement.
        equity_batch = override_tickers + list(CONFIG.universe.metals) + list(CONFIG.universe.crypto_etfs)
        try:
            tvremix_symbols = get_universe().tvremix_symbols or {}
        except Exception:
            tvremix_symbols = {}
    else:
        universe = get_universe()
        shard_index = get_shard_index(now, CONFIG.schedule.n_shards, CONFIG.schedule.shard_interval_minutes)
        shard = select_shard(universe.equities, shard_index, CONFIG.schedule.n_shards)
        equity_batch = shard + universe.metals + (universe.crypto_etfs or [])
        tvremix_symbols = universe.tvremix_symbols or {}
        logger.info(
            "shard %d/%d: %d equities + %d metals + %d crypto ETFs",
            shard_index, CONFIG.schedule.n_shards, len(shard), len(universe.metals), len(universe.crypto_etfs or []),
        )

    resolved = len(tvremix_symbols)
    total = len(equity_batch)
    if resolved == 0:
        logger.warning("tvremix corroboration unavailable this run: 0/%d tickers resolved - check TVREMIX_API_KEY", total)
    else:
        logger.info("tvremix corroboration available for %d/%d tickers this run", resolved, total)

    try:
        spreadsheet = open_spreadsheet()
        cooldown_state = load_cooldown_state(spreadsheet)
    except Exception as exc:
        logger.warning("could not open Sheets (%s) - proceeding with empty cooldown state", exc)
        spreadsheet = None
        cooldown_state = {}

    history_buffer: list[list] = []
    alert_lock = threading.Lock()

    _run_pool(scan_equity_symbol, equity_batch, EQUITY_CONCURRENCY, now, cooldown_state, alert_lock, args.dry_run, history_buffer, tvremix_symbols)

    logger.info("run complete: %d alerts sent", len(history_buffer))

    if spreadsheet is not None and not args.dry_run:
        append_history_rows(spreadsheet, history_buffer)
        flush_cooldown_state(spreadsheet, cooldown_state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
