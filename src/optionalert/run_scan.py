"""Entrypoint for scan.yml. Full flow: market-hours gate -> load universe from
cache -> pick this run's shard -> read Sheet cooldown state -> scan equities +
metals + crypto -> filter through cooldown -> send survivors -> one batched
write each to History and Cooldown.

Supports `--dry-run` (print instead of send/write) and `--tickers` (bypass
sharding, scan an explicit list) for manual workflow_dispatch testing.
"""

import argparse
import logging
import sys
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


def scan_equity_symbol(ticker, now, cooldown_state, dry_run, history_buffer):
    snapshot = fetch_underlying_snapshot(ticker)

    rows = fetch_option_chain(ticker)
    if rows:
        results = score_option_chain(rows, baseline_vol=snapshot.realized_vol_20d)
        for result in results:
            text = build_alert_text_options(result)
            sent = send_alert_if_not_cooling(result.ticker, result.kind.value, text, cooldown_state, now, dry_run)
            if sent:
                history_buffer.append(_history_row(AlertRecord(
                    timestamp_utc=now.isoformat(), ticker=result.ticker,
                    asset_class=result.asset_class.value, kind=result.kind.value,
                    score=result.score, sub_vol_oi=result.sub_vol_oi, sub_iv=result.sub_iv,
                    sub_block=result.sub_block, strike=result.strike,
                    expiry=result.expiry.isoformat(), notional_usd=result.notional_usd,
                )))

    vol_alert = check_equity_volume_anomaly(snapshot)
    if vol_alert:
        text = build_alert_text_equity_volume(vol_alert)
        sent = send_alert_if_not_cooling(ticker, "EQUITY_VOLUME", text, cooldown_state, now, dry_run)
        if sent:
            history_buffer.append(_history_row(AlertRecord(
                timestamp_utc=now.isoformat(), ticker=ticker, asset_class="EQUITY",
                kind="EQUITY_VOLUME", score=0, sub_vol_oi=0, sub_iv=0, sub_block=0,
                strike="", expiry="", notional_usd=0,
            )))


def scan_crypto_symbol(currency, now, cooldown_state, dry_run, history_buffer):
    baseline_vol = fetch_deribit_historical_volatility(currency)
    rows = get_deribit_option_chain(currency)
    if not rows:
        return

    results = score_option_chain(rows, baseline_vol=baseline_vol)
    for result in results:
        text = build_alert_text_options(result)
        sent = send_alert_if_not_cooling(result.ticker, result.kind.value, text, cooldown_state, now, dry_run)
        if sent:
            history_buffer.append(_history_row(AlertRecord(
                timestamp_utc=now.isoformat(), ticker=result.ticker,
                asset_class=result.asset_class.value, kind=result.kind.value,
                score=result.score, sub_vol_oi=result.sub_vol_oi, sub_iv=result.sub_iv,
                sub_block=result.sub_block, strike=result.strike,
                expiry=result.expiry.isoformat(), notional_usd=result.notional_usd,
            )))


def main() -> int:
    args = parse_args()
    now = datetime.now(timezone.utc)

    override_tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    if not override_tickers and not is_nyse_open(now):
        logger.info("NYSE closed - nothing to do")
        return 0

    universe = get_universe()

    if override_tickers:
        equity_batch = override_tickers
        crypto_batch = universe.crypto
    else:
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

    for ticker in equity_batch:
        try:
            scan_equity_symbol(ticker, now, cooldown_state, args.dry_run, history_buffer)
        except Exception as exc:
            logger.warning("scan failed for %s: %s", ticker, exc)

    for currency in crypto_batch:
        try:
            scan_crypto_symbol(currency, now, cooldown_state, args.dry_run, history_buffer)
        except Exception as exc:
            logger.warning("scan failed for %s: %s", currency, exc)

    logger.info("run complete: %d alerts sent", len(history_buffer))

    if spreadsheet is not None and not args.dry_run:
        append_history_rows(spreadsheet, history_buffer)
        flush_cooldown_state(spreadsheet, cooldown_state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
