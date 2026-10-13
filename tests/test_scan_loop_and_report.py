from datetime import date, datetime, timezone

import pandas as pd

from optionalert.email_report import rows_on_ny_date
from optionalert.market_hours import nyse_session
from optionalert.run_eod_report import report_day
from optionalert.run_scan import next_cycle_start


def test_rows_on_ny_date_uses_new_york_day_not_utc():
    # 2026-10-08 19:09 UTC = 15:09 ET on the 8th; 2026-10-09 01:30 UTC is
    # still the evening of the 8th in New York.
    df = pd.DataFrame({"timestamp_utc": [
        "2026-10-08T19:09:56.440769+00:00",
        "2026-10-09T01:30:00+00:00",
        "2026-10-09T18:40:00+00:00",
    ]})
    assert len(rows_on_ny_date(df, date(2026, 10, 8))) == 2
    assert len(rows_on_ny_date(df, date(2026, 10, 9))) == 1


def test_report_day_after_ny_midnight_is_previous_day():
    # GitHub fired the EOD job at 2026-10-13 02:39 UTC = 22:39 ET on the 12th.
    assert report_day(datetime(2026, 10, 13, 2, 39, tzinfo=timezone.utc)) == date(2026, 10, 12)
    # Even past New York midnight, before the next open it's still yesterday.
    assert report_day(datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)) == date(2026, 10, 12)
    # Normal on-time run: 21:30 UTC = 17:30 ET same day.
    assert report_day(datetime(2026, 10, 12, 21, 30, tzinfo=timezone.utc)) == date(2026, 10, 12)


def test_next_cycle_start_is_next_interval_boundary():
    assert next_cycle_start(datetime(2026, 10, 13, 14, 3, 20, tzinfo=timezone.utc), 10) == \
        datetime(2026, 10, 13, 14, 10, tzinfo=timezone.utc)
    assert next_cycle_start(datetime(2026, 10, 13, 14, 10, tzinfo=timezone.utc), 10) == \
        datetime(2026, 10, 13, 14, 20, tzinfo=timezone.utc)


def test_nyse_session_trading_day_and_weekend():
    open_ts, close_ts = nyse_session(datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc))
    assert (open_ts.hour, open_ts.minute, close_ts.hour) == (9, 30, 16)
    assert nyse_session(datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)) is None  # Saturday
