"""Per-(ticker, kind) alert cooldown, persisted in the Sheet's "Cooldown" tab
so it survives across separate GitHub Actions runs (each run is a fresh VM).
Read once at the start of a run, flushed once at the end - bounded API calls
regardless of how many alerts fire."""

from datetime import datetime, timedelta, timezone

from .config import CONFIG
from .sheets_client import get_or_create_worksheet

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


def mark_alerted(state: CooldownState, ticker: str, kind: str, now: datetime) -> None:
    state[(ticker, kind)] = now


def flush_cooldown_state(spreadsheet, state: CooldownState) -> None:
    ws = get_or_create_worksheet(spreadsheet, CONFIG.sheets.cooldown_tab, CONFIG.sheets.cooldown_header)
    rows = [[ticker, kind, ts.astimezone(timezone.utc).isoformat()] for (ticker, kind), ts in state.items()]
    ws.clear()
    ws.update("A1", [list(CONFIG.sheets.cooldown_header)] + rows)
