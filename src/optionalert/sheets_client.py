"""Thin gspread wrapper around the two worksheets: History (permanent alert
log) and Cooldown (transient per-ticker last-alert-time state)."""

import json
import os

from .config import CONFIG


def get_gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


def open_spreadsheet():
    client = get_gspread_client()
    return client.open_by_key(os.environ["GOOGLE_SHEET_ID"])


def get_or_create_worksheet(spreadsheet, name: str, header: tuple):
    try:
        ws = spreadsheet.worksheet(name)
    except Exception:
        ws = spreadsheet.add_worksheet(title=name, rows=1000, cols=len(header))
        ws.append_row(list(header))
        return ws

    if not ws.row_values(1):
        ws.append_row(list(header))
    return ws


def append_history_rows(spreadsheet, rows: list[list]) -> None:
    if not rows:
        return
    ws = get_or_create_worksheet(spreadsheet, CONFIG.sheets.history_tab, CONFIG.sheets.history_header)
    ws.append_rows(rows, value_input_option="RAW")
