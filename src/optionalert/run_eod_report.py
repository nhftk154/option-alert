"""Entrypoint for eod_report.yml. No-ops on weekends/holidays (checked here,
not via cron) so the schedule YAML can stay a simple weekday cron."""

import logging
import sys
from datetime import date, datetime, time, timedelta, timezone

from .email_report import (
    build_charts,
    build_summary,
    compose_email,
    fetch_today_history,
    fetch_today_positions,
    prepare_positions,
    send_email,
)
from .market_hours import NY_TZ, was_trading_day
from .sheets_client import open_spreadsheet

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def report_day(now_utc: datetime) -> date:
    """The New York trading day this run reports on. GitHub's cron was
    measured firing the 21:30 UTC schedule hours late, sometimes past New York
    midnight - before the next open, that still means the day just ended."""
    now_ny = now_utc.astimezone(NY_TZ)
    if now_ny.time() < time(9, 30):
        return now_ny.date() - timedelta(days=1)
    return now_ny.date()


def main() -> int:
    day = report_day(datetime.now(timezone.utc))
    # Noon New York time on that day, just to ask "was it a trading day".
    if not was_trading_day(datetime.combine(day, time(12, 0), tzinfo=NY_TZ)):
        logger.info("%s was not a trading day - skipping EOD report", day)
        return 0

    spreadsheet = open_spreadsheet()
    positions = prepare_positions(fetch_today_positions(spreadsheet, day))
    history = fetch_today_history(spreadsheet, day)
    equity_volume = history[history["kind"] == "EQUITY_VOLUME"] if not history.empty else history
    summary = build_summary(positions, equity_volume)
    charts = build_charts(positions)
    msg = compose_email(day, positions, equity_volume, summary, charts)
    send_email(msg)
    logger.info("EOD report for %s sent: %d positions", day, summary["total"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
