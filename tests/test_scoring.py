from datetime import date, timedelta

from optionalert.models import AssetClass, OptionContractRow, OptionKind
from optionalert.scoring import score_contract, score_option_chain


def make_row(**overrides) -> OptionContractRow:
    defaults = dict(
        ticker="AAPL",
        asset_class=AssetClass.EQUITY,
        kind=OptionKind.CALL,
        strike=230.0,
        expiry=date.today() + timedelta(days=10),
        dte=10,
        last_price=5.0,
        volume=1000.0,
        open_interest=200.0,
        iv=0.60,
        underlying_price=225.0,
        contract_id="AAPL240101C00230000",
    )
    defaults.update(overrides)
    return OptionContractRow(**defaults)


def test_below_min_notional_is_rejected():
    row = make_row(volume=1.0, underlying_price=1.0)  # notional = 1.0
    assert score_contract(row, baseline_vol=0.20) is None


def test_dte_out_of_range_is_rejected():
    row = make_row(dte=100)
    assert score_contract(row, baseline_vol=0.20) is None


def test_zero_dte_is_rejected():
    # OI only updates once/day while volume accrues intraday, so 0DTE
    # vol/oi ratios are routinely inflated and not a real signal.
    row = make_row(dte=0, volume=5000, open_interest=100, iv=0.80)
    assert score_contract(row, baseline_vol=0.20) is None


def test_open_interest_below_floor_is_rejected():
    # Near-zero OI on illiquid strikes is a noisy denominator, not a signal -
    # yfinance reports OI=0/1 routinely on far ITM/OTM legs.
    row = make_row(open_interest=1.0)
    assert score_contract(row, baseline_vol=0.20) is None


def test_high_vol_oi_and_iv_spike_scores_high():
    row = make_row(volume=5000, open_interest=100, iv=0.80)  # ratio 50x, IV way above baseline
    result = score_contract(row, baseline_vol=0.20)
    assert result is not None
    assert result.score > 90


def test_score_option_chain_keeps_only_best_per_ticker_and_kind():
    rows = [
        make_row(strike=230, volume=5000, open_interest=100, iv=0.80),  # high score
        make_row(strike=235, volume=1100, open_interest=1000, iv=0.61),  # low score, same ticker/kind
        make_row(strike=220, kind=OptionKind.PUT, volume=5000, open_interest=100, iv=0.80),
    ]
    results = score_option_chain(rows, baseline_vol=0.20)
    calls = [r for r in results if r.kind == OptionKind.CALL]
    assert len(calls) == 1
    assert calls[0].strike == 230


def test_score_option_chain_filters_below_threshold():
    rows = [make_row(volume=1001, open_interest=1000, iv=0.21)]  # barely above notional, weak signal
    results = score_option_chain(rows, baseline_vol=0.20)
    assert results == []
