"""Entrypoint for eod_report.yml. No-ops on weekends/holidays (checked here,
not via cron) so the schedule YAML can stay a simple weekday cron."""

import logging
import sys
from datetime import datetime, timezone

from .email_report import (
    build_charts,
    build_summary,
    compose_email,
    fetch_today_history,
    fetch_today_positions,
    prepare_positions,
    send_email,
)
from .market_hours import was_trading_day
from .sheets_client import open_spreadsheet

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    now = datetime.now(timezone.utc)
    if not was_trading_day(now):
        logger.info("not a trading day - skipping EOD report")
        return 0

    spreadsheet = open_spreadsheet()
    positions = prepare_positions(fetch_today_positions(spreadsheet))
    history = fetch_today_history(spreadsheet)
    equity_volume = history[history["kind"] == "EQUITY_VOLUME"] if not history.empty else history
    summary = build_summary(positions, equity_volume)
    charts = build_charts(positions)
    msg = compose_email(positions, equity_volume, summary, charts)
    send_email(msg)
    logger.info("EOD report sent: %d positions today", summary["total"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
