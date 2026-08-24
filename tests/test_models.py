from datetime import date, timedelta

from optionalert.models import AssetClass, OptionContractRow, OptionKind


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


def test_equity_notional_is_volume_times_multiplier_times_premium():
    row = make_row(volume=1000.0, last_price=5.0)
    assert row.notional_usd == 1000.0 * 100 * 5.0


def test_crypto_notional_is_premium_not_underlying_notional():
    # Deribit quotes last_price in the coin itself (e.g. 0.02 BTC), not USD -
    # the premium in USD needs both last_price and the index price. Dropping
    # last_price computes the notional value of coins controlled instead,
    # which is orders of magnitude larger for anything not deep ITM.
    row = make_row(
        asset_class=AssetClass.CRYPTO,
        volume=504.0,
        last_price=0.0011,  # BTC-denominated premium per contract
        underlying_price=78_824.0,  # USD index price
    )
    expected_premium = 504.0 * 0.0011 * 78_824.0
    assert row.notional_usd == expected_premium
    # sanity: nowhere near the (wrong) volume*underlying_price figure
    assert row.notional_usd < row.volume * row.underlying_price / 100
