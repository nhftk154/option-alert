"""Per-(ticker, kind) alert cooldown, persisted in the Sheet's "Cooldown" tab
so it survives across separate GitHub Actions runs (each run is a fresh VM).
Read once at the start of a run, flushed once at the end - bounded API calls
regardless of how many alerts fire."""

from datetime import datetime, timedelta, timezone

from .config import CONFIG
from .market_hours import NY_TZ
from .sheets_client import get_or_create_worksheet

# Entries older than this are dropped on flush. Must exceed every window the
# state is queried with (cooldown_minutes, and "same NY trading day" for
# per-contract position alerts) - otherwise the Cooldown tab would grow by one
# row per alerted contract forever.
_STATE_RETENTION = timedelta(days=3)

CooldownState = dict[tuple[str, str], datetime]


def load_cooldown_state(spreadsheet) -> CooldownState:
    ws = get_or_create_worksheet(spreadsheet, CONFIG.sheets.cooldown_tab, CONFIG.sheets.cooldown_header)
    records = ws.get_all_values()[1:]  # skip header

    state: CooldownState = {}
    for row in records:
        if len(row) < 3:
            continue
        ticker, kind, last_alert_str = row[0], row[1], row[2]
        try:
            state[(ticker, kind)] = datetime.fromisoformat(last_alert_str)
        except ValueError:
            continue
    return state


def is_on_cooldown(state: CooldownState, ticker: str, kind: str, now: datetime, minutes: int | None = None) -> bool:
    minutes = minutes if minutes is not None else CONFIG.thresholds.cooldown_minutes
    last = state.get((ticker, kind))
    if last is None:
        return False
    return now - last < timedelta(minutes=minutes)


def alerted_same_day(state: CooldownState, ticker: str, kind: str, now: datetime) -> bool:
    """True if (ticker, kind) was already alerted on the current New York
    calendar day - used for position alerts, whose volume is cumulative
    across the day, so a fixed-minutes cooldown would re-alert the same
    contract every time the window lapsed."""
    last = state.get((ticker, kind))
    if last is None:
        return False
    return last.astimezone(NY_TZ).date() == now.astimezone(NY_TZ).date()


def mark_alerted(state: CooldownState, ticker: str, kind: str, now: datetime) -> None:
    state[(ticker, kind)] = now


def flush_cooldown_state(spreadsheet, state: CooldownState) -> None:
    ws = get_or_create_worksheet(spreadsheet, CONFIG.sheets.cooldown_tab, CONFIG.sheets.cooldown_header)
    newest = max(state.values(), default=None)
    rows = [
        [ticker, kind, ts.astimezone(timezone.utc).isoformat()]
        for (ticker, kind), ts in state.items()
        if newest - ts <= _STATE_RETENTION
    ]
    ws.clear()
    ws.update("A1", [list(CONFIG.sheets.cooldown_header)] + rows)
