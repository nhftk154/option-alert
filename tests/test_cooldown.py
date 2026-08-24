from datetime import datetime, timedelta, timezone

from optionalert.cooldown import is_on_cooldown, mark_alerted


def test_no_cooldown_when_never_alerted():
    state = {}
    now = datetime.now(timezone.utc)
    assert is_on_cooldown(state, "AAPL", "CALL", now) is False


def test_on_cooldown_immediately_after_alert():
    state = {}
    now = datetime.now(timezone.utc)
    mark_alerted(state, "AAPL", "CALL", now)
    assert is_on_cooldown(state, "AAPL", "CALL", now + timedelta(minutes=5), minutes=30) is True


def test_cooldown_expires_after_window():
    state = {}
    now = datetime.now(timezone.utc)
    mark_alerted(state, "AAPL", "CALL", now)
    assert is_on_cooldown(state, "AAPL", "CALL", now + timedelta(minutes=31), minutes=30) is False


def test_cooldown_is_per_ticker_and_kind():
    state = {}
    now = datetime.now(timezone.utc)
    mark_alerted(state, "AAPL", "CALL", now)
    assert is_on_cooldown(state, "AAPL", "PUT", now, minutes=30) is False
    assert is_on_cooldown(state, "MSFT", "CALL", now, minutes=30) is False
