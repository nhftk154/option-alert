"""NYSE market-hours gating. The GitHub Actions cron window is intentionally
wide (see .github/workflows/scan.yml) and this module does the precise check,
so DST drift between Israel and US never causes a missed or spurious run."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")


def is_nyse_open(now_utc: datetime | None = None) -> bool:
    """True if NYSE regular trading hours (09:30-16:00 America/New_York) are
    currently active on a trading day. Uses pandas_market_calendars for
    holiday-accurate scheduling; falls back to a plain weekday+time check if
    that call fails, so a dependency hiccup never silently blocks the system."""
    now_utc = now_utc or datetime.now(timezone.utc)
    now_ny = now_utc.astimezone(NY_TZ)

    try:
        import pandas_market_calendars as mcal

        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(start_date=now_ny.date(), end_date=now_ny.date())
        if schedule.empty:
            return False
        open_ts = schedule.iloc[0]["market_open"].tz_convert(NY_TZ)
        close_ts = schedule.iloc[0]["market_close"].tz_convert(NY_TZ)
        return open_ts <= now_ny <= close_ts
    except Exception:
        if now_ny.weekday() >= 5:
            return False
        open_t = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
        close_t = now_ny.replace(hour=16, minute=0, second=0, microsecond=0)
        return open_t <= now_ny <= close_t


def was_trading_day(now_utc: datetime | None = None) -> bool:
    """True if `now_utc`'s NY calendar date was (or is) a NYSE trading day.
    Used by the EOD report so it no-ops on weekends/holidays."""
    now_utc = now_utc or datetime.now(timezone.utc)
    now_ny = now_utc.astimezone(NY_TZ)

    try:
        import pandas_market_calendars as mcal

        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(start_date=now_ny.date(), end_date=now_ny.date())
        return not schedule.empty
    except Exception:
        return now_ny.weekday() < 5
