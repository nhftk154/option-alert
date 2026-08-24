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


def test_notional_is_volume_times_multiplier_times_premium():
    row = make_row(volume=1000.0, last_price=5.0)
    assert row.notional_usd == 1000.0 * 100 * 5.0


def test_notional_formula_is_the_same_for_every_asset_class():
    # All tracked tickers (equities, metal ETFs, crypto-linked ETFs) are
    # plain USD-quoted exchange-listed options - there's no per-asset-class
    # unit conversion needed, unlike a raw crypto-native venue would require.
    for asset_class in AssetClass:
        row = make_row(asset_class=asset_class, volume=1000.0, last_price=5.0)
        assert row.notional_usd == 1000.0 * 100 * 5.0
