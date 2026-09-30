from datetime import date, datetime, timedelta, timezone

import pandas as pd

from optionalert.alerts import build_alert_text_big_position, build_position_digest_messages
from optionalert.cooldown import alerted_same_day, mark_alerted
from optionalert.email_report import prepare_positions, rank_by_expiry
from optionalert.models import AssetClass, OptionContractRow, OptionKind
from optionalert.positions import PositionTier, classify_premium, find_large_positions
from optionalert.telegram_client import MAX_MESSAGE_CHARS


def make_row(premium_usd: float, **overrides) -> OptionContractRow:
    # last_price=10 -> premium = volume * 1000
    defaults = dict(
        ticker="AAPL",
        asset_class=AssetClass.EQUITY,
        kind=OptionKind.CALL,
        strike=230.0,
        expiry=date.today() + timedelta(days=10),
        dte=10,
        last_price=10.0,
        volume=premium_usd / 1000,
        open_interest=500.0,
        iv=0.40,
        underlying_price=225.0,
        contract_id=f"AAPL-{premium_usd:.0f}",
    )
    defaults.update(overrides)
    return OptionContractRow(**defaults)


def test_classify_premium_tiers():
    assert classify_premium(999_999) is None
    assert classify_premium(1_000_000) == PositionTier.INFO
    assert classify_premium(4_900_000) == PositionTier.INFO
    assert classify_premium(5_000_000) == PositionTier.ALERT


def test_find_large_positions_keeps_every_contract_sorted_by_size():
    rows = [
        make_row(1_500_000, strike=230),
        make_row(6_000_000, strike=235),
        make_row(2_000_000, strike=240, kind=OptionKind.PUT),
        make_row(400_000, strike=245),  # below threshold
    ]
    hits = find_large_positions(rows, baseline_vol=0.3)
    assert [h.row.strike for h in hits] == [235, 240, 230]
    assert [h.tier for h in hits] == [PositionTier.ALERT, PositionTier.INFO, PositionTier.INFO]


def test_find_large_positions_ignores_low_open_interest_floor():
    # A brand-new $2M position on a contract with no prior OI is exactly
    # what should be reported - the score path's OI floor doesn't apply here.
    hits = find_large_positions([make_row(2_000_000, open_interest=0)], baseline_vol=0.3)
    assert len(hits) == 1


def test_find_large_positions_respects_dte_window():
    rows = [make_row(3_000_000, dte=0), make_row(3_000_000, dte=200)]
    assert find_large_positions(rows, baseline_vol=0.3) == []


def test_big_position_text_mentions_contract_and_premium():
    hit = find_large_positions([make_row(7_200_000)], baseline_vol=0.3)[0]
    text = build_alert_text_big_position(hit)
    assert "AAPL 230 C" in text
    assert "$7.2M" in text


def test_digest_splits_under_telegram_limit_and_orders_by_size():
    rows = [make_row(1_000_000 + i * 10_000, strike=100 + i, contract_id=f"C{i}") for i in range(300)]
    hits = find_large_positions(rows, baseline_vol=0.3)
    messages = build_position_digest_messages(hits)
    assert len(messages) > 1
    assert all(len(m) <= MAX_MESSAGE_CHARS for m in messages)
    assert "1. AAPL 399 C" in messages[0]  # largest premium first


def test_alerted_same_day_resets_on_next_ny_day():
    state = {}
    # 15:00 ET on day 1
    first = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)
    mark_alerted(state, "AAPL", "POS:X:INFO", first)
    assert alerted_same_day(state, "AAPL", "POS:X:INFO", first + timedelta(hours=1)) is True
    # 10:00 ET next day - under 24h later, but a new trading day
    assert alerted_same_day(state, "AAPL", "POS:X:INFO", datetime(2026, 9, 30, 14, 0, tzinfo=timezone.utc)) is False


def _positions_df(rows):
    return pd.DataFrame(rows, columns=[
        "timestamp_utc", "ticker", "kind", "tier", "strike", "expiry", "dte",
        "premium_usd", "volume", "open_interest", "last_price", "underlying_price", "iv", "contract_id",
    ])


def test_prepare_positions_dedupes_contract_and_ranks_by_size():
    df = _positions_df([
        ["t1", "AAPL", "CALL", "INFO", 230, "2026-10-09", 9, 2_000_000, 1, 1, 1, 1, 0.3, "A"],
        ["t2", "AAPL", "CALL", "ALERT", 230, "2026-10-09", 9, 5_500_000, 1, 1, 1, 1, 0.3, "A"],
        ["t3", "MSFT", "PUT", "INFO", 400, "2026-10-02", 2, 3_000_000, 1, 1, 1, 1, 0.3, "B"],
        ["t4", "NVDA", "CALL", "INFO", 150, "2026-11-20", 51, 3_000_000, 1, 1, 1, 1, 0.3, "C"],
    ])
    ranked = prepare_positions(df)
    assert list(ranked["contract_id"]) == ["A", "B", "C"]  # size, then nearer expiry on a tie
    assert ranked.loc[0, "premium_usd"] == 5_500_000

    by_expiry = rank_by_expiry(ranked)
    assert list(by_expiry["contract_id"]) == ["B", "A", "C"]
