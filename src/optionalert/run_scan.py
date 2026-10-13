"""Entrypoint for scan.yml. Full flow: market-hours gate -> load universe from
cache -> pick this run's shard -> read Sheet cooldown state -> scan the shard
(one thread pool, see below) -> large-position and stock-volume checks,
filtered through cooldown -> send survivors -> one batched write each to
Positions, History and Cooldown.

Position alerts come in two tiers (see positions.py): a contract whose premium
today crosses position_alert_usd gets its own loud alert immediately; ones
between position_info_usd and that go into a single silent digest sent at the
end of the run. Each contract is reported at most once per tier per day.

Supports `--dry-run` (print instead of send/write) and `--tickers` (bypass
sharding, scan an explicit list, one cycle) for manual workflow_dispatch
testing.

`--loop` is how scan.yml runs on schedule: GitHub's cron is best-effort and
was measured firing only 1-3 times a day, hours late, against a */10
schedule - so one started job instead keeps scanning every
shard_interval_minutes itself until the close (waiting for the open first if
it started early), and the schedule only has to fire once in time.

Concurrency note: fetching each ticker is independent network I/O, so the
whole batch is scanned with one thread pool. Measured: ~10s/ticker
sequentially, which would make a 50-ticker shard alone take ~8 minutes -
parallelized to 8 workers instead. `cooldown_state`, `history_buffer`,
`position_buffer` and `digest_hits` are mutated from multiple worker threads,
so every write to them goes through `_alert_lock`.
"""

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .alerts import (
    build_alert_text_big_position,
    build_alert_text_equity_volume,
    build_position_digest_messages,
    send_alert_if_not_cooling,
)
from .config import CONFIG
from .cooldown import alerted_same_day, flush_cooldown_state, load_cooldown_state, mark_alerted
from .data_equity import fetch_option_chain, fetch_underlying_snapshot
from .equity_volume import check_equity_volume_anomaly
from .market_hours import is_nyse_open, nyse_session
from .models import AlertRecord
from .positions import PositionHit, PositionTier, find_large_positions
from .sharding import get_shard_index, select_shard
from .sheets_client import append_history_rows, append_position_rows, open_spreadsheet
from .telegram_client import send_telegram_message
from .universe import get_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EQUITY_CONCURRENCY = 8  # moderate - yfinance is an unofficial/fragile scraper

# GitHub Actions kills a job at 6h; stop looping comfortably before that so
# the final cycle's Sheet writes always land. The next scheduled run (queued
# behind this one by scan.yml's concurrency group) picks up from there.
LOOP_MAX_RUNTIME = timedelta(hours=5, minutes=40)
# How long --loop will wait for the open if it was started before it.
LOOP_MAX_PREOPEN_WAIT = timedelta(hours=4)
# Written when a loop stops on LOOP_MAX_RUNTIME before the close; scan.yml
# then dispatches the next loop run itself.
HANDOFF_MARKER = "scan_handoff_needed"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tickers", default="", help="Comma-separated override, bypasses sharding")
    parser.add_argument("--loop", action="store_true", help="Keep scanning every shard interval until the close")
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


def _position_row(hit: PositionHit, now) -> list:
    row = hit.row
    return [
        now.isoformat(), row.ticker, row.kind.value, hit.tier.value, row.strike,
        row.expiry.isoformat(), row.dte, round(hit.premium_usd), row.volume,
        row.open_interest, row.last_price, row.underlying_price, round(row.iv, 4),
        row.contract_id,
    ]


def _position_cooldown_key(hit: PositionHit, tier: PositionTier) -> str:
    return f"POS:{hit.row.contract_id}:{tier.value}"


def _handle_position(hit, now, cooldown_state, alert_lock, dry_run, position_buffer, digest_hits):
    """Thread-safe: an ALERT-tier contract is sent right away (once per day);
    an INFO-tier one is queued for the end-of-run digest (once per day, and
    never after the same contract already got an ALERT)."""
    ticker = hit.row.ticker
    alert_key = _position_cooldown_key(hit, PositionTier.ALERT)
    info_key = _position_cooldown_key(hit, PositionTier.INFO)

    with alert_lock:
        if alerted_same_day(cooldown_state, ticker, alert_key, now):
            return
        if hit.tier == PositionTier.ALERT:
            text = build_alert_text_big_position(hit)
            if dry_run:
                print(f"[DRY RUN] {text}\n")
            else:
                send_telegram_message(text)
            mark_alerted(cooldown_state, ticker, alert_key, now)
        else:
            if alerted_same_day(cooldown_state, ticker, info_key, now):
                return
            digest_hits.append(hit)
            mark_alerted(cooldown_state, ticker, info_key, now)
        position_buffer.append(_position_row(hit, now))


def scan_equity_symbol(ticker, now, cooldown_state, alert_lock, dry_run, history_buffer, position_buffer, digest_hits):
    snapshot = fetch_underlying_snapshot(ticker)

    rows = fetch_option_chain(ticker)
    for hit in find_large_positions(rows, baseline_vol=snapshot.realized_vol_20d):
        _handle_position(hit, now, cooldown_state, alert_lock, dry_run, position_buffer, digest_hits)

    vol_alert = check_equity_volume_anomaly(snapshot)
    if vol_alert:
        text = build_alert_text_equity_volume(vol_alert)
        rec = AlertRecord(
            timestamp_utc=now.isoformat(), ticker=ticker, asset_class="EQUITY",
            kind="EQUITY_VOLUME", score=0, sub_vol_oi=0, sub_iv=0, sub_block=0,
            strike="", expiry="", notional_usd=0,
        )
        _record_alert_if_due(ticker, "EQUITY_VOLUME", text, rec, cooldown_state, alert_lock, now, dry_run, history_buffer)


def _send_digest(digest_hits, dry_run):
    for text in build_position_digest_messages(digest_hits):
        if dry_run:
            print(f"[DRY RUN] {text}\n")
        else:
            send_telegram_message(text, silent=True)


def _run_pool(fn, items, max_workers, *extra_args):
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fn, item, *extra_args): item for item in items}
        for future in futures:
            item = futures[future]
            try:
                future.result()
            except Exception as exc:
                logger.warning("scan failed for %s: %s", item, exc)


def _batch_for(now, override_tickers, universe) -> list[str]:
    # ETFs come from config, not the cache, so changing them in config takes
    # effect immediately rather than after the next weekly universe refresh.
    etfs = list(CONFIG.universe.metals) + list(CONFIG.universe.crypto_etfs)

    if override_tickers:
        # Manual smoke-test path: doesn't need the committed universe cache.
        return override_tickers + etfs

    # The cache is market-cap sorted, so slicing here also narrows an older,
    # larger cache to sp_top_n without waiting for a refresh.
    equities = universe.equities[:CONFIG.universe.sp_top_n]
    shard_index = get_shard_index(now, CONFIG.schedule.n_shards, CONFIG.schedule.shard_interval_minutes)
    shard = select_shard(equities, shard_index, CONFIG.schedule.n_shards)
    logger.info("shard %d/%d: %d equities + %d ETFs", shard_index, CONFIG.schedule.n_shards, len(shard), len(etfs))
    return shard + etfs


def run_cycle(now, batch, spreadsheet, cooldown_state, dry_run) -> None:
    """One scan of `batch`: send alerts, then write Positions/History and
    the updated cooldown state, so the Sheet (and the EOD report reading it)
    is current after every cycle, not only when a long --loop job ends."""
    history_buffer: list[list] = []
    position_buffer: list[list] = []
    digest_hits: list[PositionHit] = []
    alert_lock = threading.Lock()

    _run_pool(
        scan_equity_symbol, batch, EQUITY_CONCURRENCY,
        now, cooldown_state, alert_lock, dry_run, history_buffer, position_buffer, digest_hits,
    )
    _send_digest(digest_hits, dry_run)

    logger.info(
        "cycle complete: %d new positions (%d in digest), %d stock-volume alerts",
        len(position_buffer), len(digest_hits), len(history_buffer),
    )

    if spreadsheet is not None and not dry_run:
        append_position_rows(spreadsheet, position_buffer)
        append_history_rows(spreadsheet, history_buffer)
        flush_cooldown_state(spreadsheet, cooldown_state)


def next_cycle_start(now: datetime, interval_minutes: int) -> datetime:
    """The next wall-clock boundary of the shard interval (e.g. :00, :10,
    :20...), so consecutive cycles land in consecutive shard buckets."""
    interval = interval_minutes * 60
    return datetime.fromtimestamp((int(now.timestamp()) // interval + 1) * interval, tz=timezone.utc)


def _sleep_until(target: datetime) -> None:
    delay = (target - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        time.sleep(delay)


def _open_sheets():
    try:
        spreadsheet = open_spreadsheet()
        return spreadsheet, load_cooldown_state(spreadsheet)
    except Exception as exc:
        logger.warning("could not open Sheets (%s) - proceeding with empty cooldown state", exc)
        return None, {}


def run_loop(dry_run: bool) -> int:
    started = datetime.now(timezone.utc)
    session = nyse_session(started)
    if session is None:
        logger.info("not a trading day - nothing to do")
        return 0
    open_ts, close_ts = session
    if started > close_ts:
        logger.info("NYSE already closed for today - nothing to do")
        return 0
    if open_ts - started > LOOP_MAX_PREOPEN_WAIT:
        logger.info("started %s before the open - too early, exiting", open_ts - started)
        return 0
    if started < open_ts:
        logger.info("waiting %s for the open", open_ts - started)
        _sleep_until(open_ts)

    universe = get_universe()
    spreadsheet, cooldown_state = _open_sheets()
    deadline = min(close_ts, started + LOOP_MAX_RUNTIME)
    interval = CONFIG.schedule.shard_interval_minutes

    while True:
        now = datetime.now(timezone.utc)
        if now > deadline:
            break
        try:
            run_cycle(now, _batch_for(now, [], universe), spreadsheet, cooldown_state, dry_run)
        except Exception as exc:
            # One bad cycle (Sheets/Telegram hiccup) must not end the whole
            # trading day's scanning.
            logger.exception("cycle failed: %s", exc)
        next_start = next_cycle_start(datetime.now(timezone.utc), interval)
        if next_start > deadline:
            break
        _sleep_until(next_start)

    if datetime.now(timezone.utc) >= close_ts:
        logger.info("loop finished (market closed)")
    else:
        # Stopped on LOOP_MAX_RUNTIME with the market still open: ask
        # scan.yml to start the next loop rather than hoping a cron fires.
        logger.info("loop finished (runtime limit) - requesting a follow-up run")
        Path(HANDOFF_MARKER).touch()
    return 0


def main() -> int:
    args = parse_args()
    override_tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    if args.loop and not override_tickers:
        return run_loop(args.dry_run)

    now = datetime.now(timezone.utc)
    if not override_tickers and not is_nyse_open(now):
        logger.info("NYSE closed - nothing to do")
        return 0

    universe = None if override_tickers else get_universe()
    spreadsheet, cooldown_state = _open_sheets()
    run_cycle(now, _batch_for(now, override_tickers, universe), spreadsheet, cooldown_state, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
