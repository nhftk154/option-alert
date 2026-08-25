from optionalert import bs_iv
from optionalert.models import OptionKind


def test_round_trip_call():
    sigma = 0.45
    price = bs_iv._bs_price(S=225.0, K=230.0, T=10 / 365, sigma=sigma, kind=OptionKind.CALL)
    recovered = bs_iv.implied_vol(price, S=225.0, K=230.0, dte=10, kind=OptionKind.CALL)
    assert abs(recovered - sigma) < 1e-3


def test_round_trip_put():
    sigma = 0.35
    price = bs_iv._bs_price(S=225.0, K=220.0, T=20 / 365, sigma=sigma, kind=OptionKind.PUT)
    recovered = bs_iv.implied_vol(price, S=225.0, K=220.0, dte=20, kind=OptionKind.PUT)
    assert abs(recovered - sigma) < 1e-3


def test_thin_time_value_deep_itm_near_expiry_returns_none():
    # Mirrors the real TSLA alert: 1 DTE, deep ITM put, ~15.5 of the price is
    # pure intrinsic value and time value is under 2% of price - exactly the
    # near-zero-vega regime where a solved sigma would be numerically
    # meaningless (and where a plain European model misses the American
    # early-exercise premium). The guard should decline rather than return a
    # falsely-precise number.
    sigma = 0.51
    price = bs_iv._bs_price(S=354.5, K=370.0, T=1 / 365, sigma=sigma, kind=OptionKind.PUT)
    recovered = bs_iv.implied_vol(price, S=354.5, K=370.0, dte=1, kind=OptionKind.PUT)
    assert recovered is None


def test_round_trip_itm_with_healthy_time_value():
    # Still ITM, but enough DTE that time value isn't a thin sliver of price -
    # the solver should work normally here.
    sigma = 0.45
    price = bs_iv._bs_price(S=225.0, K=235.0, T=30 / 365, sigma=sigma, kind=OptionKind.PUT)
    recovered = bs_iv.implied_vol(price, S=225.0, K=235.0, dte=30, kind=OptionKind.PUT)
    assert abs(recovered - sigma) < 1e-3


def test_price_below_intrinsic_returns_none():
    # A put struck at 370 with spot at 354.5 has intrinsic value 15.5 - any
    # quoted price below that isn't a valid no-arbitrage option price.
    assert bs_iv.implied_vol(10.0, S=354.5, K=370.0, dte=1, kind=OptionKind.PUT) is None


def test_non_positive_inputs_return_none():
    assert bs_iv.implied_vol(5.0, S=225.0, K=230.0, dte=0, kind=OptionKind.CALL) is None
    assert bs_iv.implied_vol(0.0, S=225.0, K=230.0, dte=10, kind=OptionKind.CALL) is None
