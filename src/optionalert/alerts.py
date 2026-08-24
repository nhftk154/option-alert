"""Alert message templates (English, "Hot Contract" card style) and send
orchestration. Kept independent from the score/volume computation
(scoring.py, equity_volume.py) so message formatting can change without
touching detection logic."""

from datetime import datetime, timezone

from .config import CONFIG
from .cooldown import CooldownState, is_on_cooldown, mark_alerted
from .models import AssetClass, EquityVolumeAlert, OptionKind, ScoreResult
from .telegram_client import send_telegram_message


def severity_emoji(score: float) -> str:
    return "🔴" if score >= CONFIG.thresholds.severity_extreme else "🟡"


def yahoo_option_chain_url(ticker: str, asset_class: AssetClass) -> str:
    if asset_class == AssetClass.CRYPTO:
        return f"https://www.deribit.com/{ticker.lower()}/options"
    return f"https://finance.yahoo.com/quote/{ticker}/options"


def build_alert_text_options(result: ScoreResult) -> str:
    emoji = severity_emoji(result.score)
    kind_letter = "C" if result.kind == OptionKind.CALL else "P"
    link = yahoo_option_chain_url(result.ticker, result.asset_class)

    # Signed distance from the current price - deliberately not labeled
    # ITM/OTM, since that sign flips between calls and puts and a plain
    # distance is unambiguous for both.
    distance_pct = (result.strike - result.underlying_price) / result.underlying_price * 100

    # Deribit premiums are coin-denominated; equities/metals are already USD.
    avg_fill_usd = (
        result.last_price * result.underlying_price
        if result.asset_class == AssetClass.CRYPTO
        else result.last_price
    )

    return (
        f"{emoji} Hot Contract: {result.ticker}\n"
        f"{result.ticker} {result.strike:g} {kind_letter} {result.expiry.isoformat()} ({result.dte} DTE)\n"
        f"\n"
        f"Overall Volume: {result.volume:,.0f}\n"
        f"Open Interest: {result.open_interest:,.0f}\n"
        f"Vol/OI: {result.vol_oi_ratio:.1f}x\n"
        f"Distance from price: {distance_pct:+.0f}%\n"
        f"Premium: ${result.notional_usd:,.0f}\n"
        f"Last Fill: ${avg_fill_usd:,.2f}\n"
        f"IV: {result.iv * 100:.0f}% vs baseline {result.baseline_vol * 100:.0f}%\n"
        f"Anomaly Score: {result.score:.0f}/100\n"
        f"\n"
        f"Manual check: {link}"
    )


def build_alert_text_equity_volume(alert: EquityVolumeAlert) -> str:
    emoji = "🟡"
    link = f"https://finance.yahoo.com/quote/{alert.ticker}"
    return (
        f"{emoji} Unusual Stock Volume: {alert.ticker}\n"
        f"Today's Volume: {alert.today_volume:,.0f}\n"
        f"20-Day Average: {alert.avg_volume_20d:,.0f}\n"
        f"Ratio: {alert.ratio:.1f}x average\n"
        f"Manual check: {link}"
    )


def send_alert_if_not_cooling(
    ticker: str,
    kind: str,
    text: str,
    cooldown_state: CooldownState,
    now: datetime | None = None,
    dry_run: bool = False,
) -> bool:
    """Returns True if the alert was (or would be, in dry-run) sent."""
    now = now or datetime.now(timezone.utc)
    if is_on_cooldown(cooldown_state, ticker, kind, now):
        return False

    if dry_run:
        print(f"[DRY RUN] {text}\n")
    else:
        send_telegram_message(text)

    mark_alerted(cooldown_state, ticker, kind, now)
    return True
