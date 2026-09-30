"""Alert message templates (English, "Hot Contract" card style) and send
orchestration. Kept independent from the score/volume computation
(scoring.py, equity_volume.py) so message formatting can change without
touching detection logic."""

from datetime import datetime, timezone

from .config import CONFIG
from .cooldown import CooldownState, is_on_cooldown, mark_alerted
from .models import EquityVolumeAlert, OptionKind, OptionContractRow, ScoreResult
from .positions import PositionHit
from .telegram_client import MAX_MESSAGE_CHARS, send_telegram_message


def severity_emoji(score: float) -> str:
    return "🔴" if score >= CONFIG.thresholds.severity_extreme else "🟡"


def yahoo_option_chain_url(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{ticker}/options"


def build_alert_text_options(result: ScoreResult) -> str:
    emoji = severity_emoji(result.score)
    kind_letter = "C" if result.kind == OptionKind.CALL else "P"
    link = yahoo_option_chain_url(result.ticker)

    # Signed distance from the current price - deliberately not labeled
    # ITM/OTM, since that sign flips between calls and puts and a plain
    # distance is unambiguous for both.
    distance_pct = (result.strike - result.underlying_price) / result.underlying_price * 100

    return (
        f"{emoji} Hot Contract: {result.ticker}\n"
        f"{result.ticker} {result.strike:g} {kind_letter} {result.expiry.isoformat()} ({result.dte} DTE)\n"
        f"\n"
        f"Overall Volume: {result.volume:,.0f}\n"
        f"Open Interest: {result.open_interest:,.0f}\n"
        f"Vol/OI: {result.vol_oi_ratio:.1f}x\n"
        f"Distance from price: {distance_pct:+.0f}%\n"
        f"Premium: ${result.notional_usd:,.0f}\n"
        f"Last Fill: ${result.last_price:,.2f}\n"
        f"IV: {result.iv * 100:.0f}% vs baseline {result.baseline_vol * 100:.0f}%\n"
        f"Anomaly Score: {result.score:.0f}/100\n"
        f"\n"
        f"Manual check: {link}"
    )


def _contract_label(row: OptionContractRow) -> str:
    kind_letter = "C" if row.kind == OptionKind.CALL else "P"
    return f"{row.ticker} {row.strike:g} {kind_letter} {row.expiry.isoformat()} ({row.dte} DTE)"


def _format_millions(usd: float) -> str:
    return f"${usd / 1_000_000:.1f}M"


def build_alert_text_big_position(hit: PositionHit) -> str:
    row = hit.row
    distance_pct = (row.strike - row.underlying_price) / row.underlying_price * 100
    return (
        f"🚨 Big Position: {row.ticker} {_format_millions(hit.premium_usd)}\n"
        f"{_contract_label(row)}\n"
        f"\n"
        f"Premium today: ${hit.premium_usd:,.0f}\n"
        f"Overall Volume: {row.volume:,.0f}\n"
        f"Open Interest: {row.open_interest:,.0f}\n"
        f"Vol/OI: {hit.vol_oi_ratio:.1f}x\n"
        f"Distance from price: {distance_pct:+.0f}%\n"
        f"Last Fill: ${row.last_price:,.2f}\n"
        f"IV: {row.iv * 100:.0f}% vs baseline {hit.baseline_vol * 100:.0f}%\n"
        f"\n"
        f"Manual check: {yahoo_option_chain_url(row.ticker)}"
    )


def build_position_digest_messages(hits: list[PositionHit]) -> list[str]:
    """One silent digest for all info-tier positions found this run, largest
    premium first, split into as many messages as Telegram's length cap
    requires."""
    if not hits:
        return []
    thresholds = CONFIG.thresholds
    header = (
        f"🟡 Large positions {_format_millions(thresholds.position_info_usd)}-"
        f"{_format_millions(thresholds.position_alert_usd)} ({len(hits)} new)\n"
    )
    lines = [
        f"{i}. {_contract_label(h.row)} - {_format_millions(h.premium_usd)} (Vol/OI {h.vol_oi_ratio:.1f}x)"
        for i, h in enumerate(sorted(hits, key=lambda h: h.premium_usd, reverse=True), start=1)
    ]

    messages = []
    current = header
    for line in lines:
        if len(current) + len(line) + 1 > MAX_MESSAGE_CHARS:
            messages.append(current.rstrip("\n"))
            current = ""
        current += line + "\n"
    messages.append(current.rstrip("\n"))
    return messages


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
