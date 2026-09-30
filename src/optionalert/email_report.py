"""Daily EOD email: today's large positions ranked by size and by expiry, plus
stock-volume alerts, summary stats and charts, in Hebrew, sent via Gmail SMTP
with an App Password."""

import html
import io
import os
import smtplib
from datetime import date, datetime, timezone
from email.message import EmailMessage

from .config import CONFIG
from .sheets_client import get_or_create_worksheet

_NUMERIC_POSITION_COLUMNS = (
    "strike", "dte", "premium_usd", "volume", "open_interest", "last_price", "underlying_price", "iv",
)
# Expiry buckets for the by-expiry table/chart, as (label, max DTE inclusive).
_DTE_BUCKETS = (("עד שבוע", 7), ("1-2 שבועות", 14), ("2-4 שבועות", 30), ("מעל חודש", 10_000))


def _today_rows(spreadsheet, tab: str, header: tuple, today: date):
    import pandas as pd

    ws = get_or_create_worksheet(spreadsheet, tab, header)
    df = pd.DataFrame(ws.get_all_records())
    if df.empty:
        return df
    df["date"] = df["timestamp_utc"].astype(str).str.slice(0, 10)
    return df[df["date"] == today.isoformat()].copy()


def fetch_today_positions(spreadsheet, today: date | None = None):
    today = today or datetime.now(timezone.utc).date()
    return _today_rows(spreadsheet, CONFIG.sheets.positions_tab, CONFIG.sheets.positions_header, today)


def fetch_today_history(spreadsheet, today: date | None = None):
    today = today or datetime.now(timezone.utc).date()
    return _today_rows(spreadsheet, CONFIG.sheets.history_tab, CONFIG.sheets.history_header, today)


def prepare_positions(df):
    """One row per contract (a contract logged as INFO and later as ALERT the
    same day keeps its larger, later reading), numeric columns coerced, sorted
    largest premium first with the nearest expiry breaking ties."""
    import pandas as pd

    if df.empty:
        return df
    df = df.copy()
    for col in _NUMERIC_POSITION_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("premium_usd", ascending=False).drop_duplicates("contract_id", keep="first")
    return df.sort_values(["premium_usd", "dte"], ascending=[False, True]).reset_index(drop=True)


def rank_by_expiry(df):
    """Nearest expiry first, largest premium first within the same expiry."""
    if df.empty:
        return df
    return df.sort_values(["dte", "premium_usd"], ascending=[True, False]).reset_index(drop=True)


def dte_bucket(dte: float) -> str:
    for label, max_dte in _DTE_BUCKETS:
        if dte <= max_dte:
            return label
    return _DTE_BUCKETS[-1][0]


def build_summary(positions, equity_volume) -> dict:
    if positions.empty:
        return {
            "total": 0, "big": 0, "total_premium": 0.0, "call_premium": 0.0, "put_premium": 0.0,
            "top_ticker": None, "equity_volume": len(equity_volume),
        }
    by_ticker = positions.groupby("ticker")["premium_usd"].sum()
    return {
        "total": len(positions),
        "big": int((positions["premium_usd"] >= CONFIG.thresholds.position_alert_usd).sum()),
        "total_premium": float(positions["premium_usd"].sum()),
        "call_premium": float(positions.loc[positions["kind"] == "CALL", "premium_usd"].sum()),
        "put_premium": float(positions.loc[positions["kind"] == "PUT", "premium_usd"].sum()),
        "top_ticker": by_ticker.idxmax(),
        "equity_volume": len(equity_volume),
    }


def _png(fig) -> bytes:
    import matplotlib.pyplot as plt

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def build_charts(positions) -> list[tuple[str, bytes]]:
    """Returns [(content_id, png_bytes), ...]."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if positions.empty:
        return []

    charts = []

    top20 = positions.head(20)
    labels = top20["ticker"] + " " + top20["strike"].map("{:g}".format) + " " + top20["kind"].str[0] + " " + top20["expiry"].astype(str)
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.barh(labels, top20["premium_usd"] / 1_000_000)
    ax.invert_yaxis()
    ax.set_xlabel("Premium ($M)")
    ax.set_title("Top 20 positions today by size")
    charts.append(("chart_top20", _png(fig)))

    order = [label for label, _ in _DTE_BUCKETS]
    by_bucket = (positions["premium_usd"] / 1_000_000).groupby(positions["dte"].map(dte_bucket)).sum().reindex(order, fill_value=0)
    fig, ax = plt.subplots(figsize=(6, 4))
    # Hebrew labels render reversed in matplotlib, so the chart uses DTE ranges.
    ax.bar(["<=7d", "8-14d", "15-30d", ">30d"], by_bucket.to_numpy())
    ax.set_ylabel("Premium ($M)")
    ax.set_title("Premium by time to expiry")
    charts.append(("chart_expiry", _png(fig)))

    return charts


def _money(usd: float) -> str:
    return f"${usd / 1_000_000:.2f}M"


def _positions_table(df, limit: int) -> str:
    big = CONFIG.thresholds.position_alert_usd
    big_style = ' style="background:#fde2e2"'
    rows_html = "".join(
        f"<tr{big_style if r.premium_usd >= big else ''}>"
        f"<td>{i}</td><td>{html.escape(str(r.ticker))}</td><td>{r.kind}</td><td>{r.strike:g}</td>"
        f"<td>{r.expiry}</td><td>{r.dte:.0f}</td><td><b>{_money(r.premium_usd)}</b></td>"
        f"<td>{r.volume:,.0f}</td><td>{r.open_interest:,.0f}</td></tr>"
        for i, r in enumerate(df.head(limit).itertuples(), start=1)
    )
    return (
        '<table dir="rtl" border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse">'
        "<tr><th>#</th><th>טיקר</th><th>סוג</th><th>סטרייק</th><th>פקיעה</th><th>ימים לפקיעה</th>"
        "<th>פרמיה</th><th>נפח</th><th>OI</th></tr>"
        f"{rows_html}</table>"
    )


def _equity_volume_html(equity_volume) -> str:
    if equity_volume.empty:
        return ""
    tickers = ", ".join(html.escape(t) for t in equity_volume["ticker"].astype(str).unique())
    return f"<h3>נפח מסחר חריג במניה (פי {CONFIG.thresholds.equity_volume_multiplier:g}+ מהממוצע)</h3><p>{tickers}</p>"


def compose_email(positions, equity_volume, summary: dict, charts: list[tuple[str, bytes]], table_limit: int = 50) -> EmailMessage:
    msg = EmailMessage()
    today_str = datetime.now(timezone.utc).date().isoformat()
    msg["Subject"] = f"דוח יומי - פוזיציות גדולות באופציות ({today_str})"
    msg["From"] = os.environ["EMAIL_ADDRESS"]
    msg["To"] = os.environ["EMAIL_ADDRESS"]

    thresholds = CONFIG.thresholds
    summary_html = (
        f"<p>פוזיציות מעל {_money(thresholds.position_info_usd)}: <b>{summary['total']}</b> | "
        f"מתוכן מעל {_money(thresholds.position_alert_usd)}: <b>{summary['big']}</b><br>"
        f"סה\"כ פרמיה: {_money(summary['total_premium'])} "
        f"(CALL {_money(summary['call_premium'])} / PUT {_money(summary['put_premium'])})<br>"
        f"הטיקר עם הכי הרבה פרמיה: {html.escape(str(summary.get('top_ticker') or '-'))}</p>"
    )

    if positions.empty:
        tables_html = "<p>לא נמצאו היום פוזיציות מעל הסף.</p>"
    else:
        tables_html = (
            f"<h3>דירוג לפי גודל פוזיציה</h3>{_positions_table(positions, table_limit)}"
            f"<h3>דירוג לפי זמן פקיעה (הקרובה ראשונה)</h3>{_positions_table(rank_by_expiry(positions), table_limit)}"
        )
        if len(positions) > table_limit:
            tables_html += f"<p>מוצגות {table_limit} הראשונות מתוך {len(positions)}. הרשימה המלאה בטאב \"{CONFIG.sheets.positions_tab}\" בגיליון.</p>"

    images_html = "".join(f'<p><img src="cid:{cid}"></p>' for cid, _ in charts)

    html_body = (
        f'<div dir="rtl" style="font-family:Arial,sans-serif;text-align:right">'
        f"<h2>דוח יומי - פוזיציות גדולות באופציות</h2>"
        f"{summary_html}"
        f"{tables_html}"
        f"{_equity_volume_html(equity_volume)}"
        f"{images_html}"
        f"<p style=\"color:#666;font-size:12px\">פרמיה = סך הפרמיה שנסחרה היום בחוזה (נפח × 100 × מחיר אחרון), "
        f"לא עסקה בודדת. שורות אדומות: מעל {_money(thresholds.position_alert_usd)}.</p>"
        f"</div>"
    )

    msg.set_content("גרסת טקסט: ראה מייל HTML לתצוגה מלאה.")
    msg.add_alternative(html_body, subtype="html")

    html_part = msg.get_payload()[-1]
    for cid, png_bytes in charts:
        html_part.add_related(png_bytes, maintype="image", subtype="png", cid=f"<{cid}>")

    return msg


def send_email(msg: EmailMessage) -> None:
    address = os.environ["EMAIL_ADDRESS"]
    app_password = os.environ["EMAIL_APP_PASSWORD"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(address, app_password)
        smtp.send_message(msg)
