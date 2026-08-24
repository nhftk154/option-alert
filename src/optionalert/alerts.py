"""Hebrew alert message templates and send orchestration. Kept independent
from the score/volume computation (scoring.py, equity_volume.py) so message
formatting can change without touching detection logic."""

from datetime import datetime, timezone

from .config import CONFIG
from .cooldown import CooldownState, is_on_cooldown, mark_alerted
from .models import AssetClass, EquityVolumeAlert, OptionKind, ScoreResult
from .telegram_client import send_telegram_message

_KIND_HEBREW = {OptionKind.CALL: "קול (CALL)", OptionKind.PUT: "פוט (PUT)"}


def severity_emoji(score: float) -> str:
    return "🔴" if score >= CONFIG.thresholds.severity_extreme else "🟡"


def yahoo_option_chain_url(ticker: str, asset_class: AssetClass) -> str:
    if asset_class == AssetClass.CRYPTO:
        return f"https://www.deribit.com/{ticker.lower()}/options"
    return f"https://finance.yahoo.com/quote/{ticker}/options"


def build_alert_text_options(result: ScoreResult) -> str:
    emoji = severity_emoji(result.score)
    kind_he = _KIND_HEBREW[result.kind]
    link = yahoo_option_chain_url(result.ticker, result.asset_class)
    return (
        f"{emoji} פעילות אופציות חריגה: {result.ticker}\n"
        f"סוג: {kind_he}\n"
        f"ציון חריגות: {result.score:.0f}/100\n"
        f"נפח/ריבית פתוחה: {result.vol_oi_ratio:.1f}x\n"
        f"תנודתיות גלומה: {result.iv * 100:.0f}% מול קו בסיס {result.baseline_vol * 100:.0f}%\n"
        f"ערך נקוב: ${result.notional_usd:,.0f}\n"
        f"פקיעה: {result.expiry.isoformat()} | סטרייק: {result.strike:g}\n"
        f"לבדיקה ידנית: {link}"
    )


def build_alert_text_equity_volume(alert: EquityVolumeAlert) -> str:
    emoji = "🟡"
    link = f"https://finance.yahoo.com/quote/{alert.ticker}"
    return (
        f"{emoji} נפח מסחר חריג במניה: {alert.ticker}\n"
        f"נפח היום: {alert.today_volume:,.0f}\n"
        f"ממוצע 20 יום: {alert.avg_volume_20d:,.0f}\n"
        f"יחס: פי {alert.ratio:.1f} מהממוצע\n"
        f"לבדיקה ידנית: {link}"
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
