from datetime import datetime, timezone

from optionalert.market_hours import is_nyse_open, was_trading_day


def test_open_during_regular_hours_edt():
    # 2024-07-15 (Monday, EDT) 15:00 UTC = 11:00 ET -> market open
    now = datetime(2024, 7, 15, 15, 0, tzinfo=timezone.utc)
    assert is_nyse_open(now) is True


def test_closed_before_open_edt():
    # 2024-07-15 12:00 UTC = 08:00 ET -> before open
    now = datetime(2024, 7, 15, 12, 0, tzinfo=timezone.utc)
    assert is_nyse_open(now) is False


def test_open_during_regular_hours_est():
    # 2024-01-08 (Monday, EST, not a holiday) 16:00 UTC = 11:00 ET -> market open
    now = datetime(2024, 1, 8, 16, 0, tzinfo=timezone.utc)
    assert is_nyse_open(now) is True


def test_closed_on_weekend():
    # 2024-07-13 is a Saturday
    now = datetime(2024, 7, 13, 15, 0, tzinfo=timezone.utc)
    assert is_nyse_open(now) is False


def test_closed_on_known_holiday():
    # 2024-07-04, Independence Day
    now = datetime(2024, 7, 4, 15, 0, tzinfo=timezone.utc)
    assert is_nyse_open(now) is False


def test_was_trading_day_true_on_weekday():
    now = datetime(2024, 7, 15, 15, 0, tzinfo=timezone.utc)
    assert was_trading_day(now) is True


def test_was_trading_day_false_on_weekend():
    now = datetime(2024, 7, 13, 15, 0, tzinfo=timezone.utc)
    assert was_trading_day(now) is False
