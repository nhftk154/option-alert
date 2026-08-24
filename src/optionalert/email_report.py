"""Daily EOD email: ranked list of today's alerts + summary stats + charts,
in Hebrew, sent via Gmail SMTP with an App Password."""

import io
import os
import smtplib
from datetime import date, datetime, timezone
from email.message import EmailMessage

from .config import CONFIG
from .sheets_client import get_or_create_worksheet


def fetch_today_history(spreadsheet, today: date | None = None):
    import pandas as pd

    today = today or datetime.now(timezone.utc).date()
    ws = get_or_create_worksheet(spreadsheet, CONFIG.sheets.history_tab, CONFIG.sheets.history_header)
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    if df.empty:
        return df

    df["date"] = df["timestamp_utc"].str.slice(0, 10)
    return df[df["date"] == today.isoformat()].copy()


def build_summary(df) -> dict:
    if df.empty:
        return {"total": 0, "by_kind": {}, "by_asset_class": {}, "avg_score": 0, "top_ticker": None}

    numeric_scores = df["score"].astype(float)
    return {
        "total": len(df),
        "by_kind": df["kind"].value_counts().to_dict(),
        "by_asset_class": df["asset_class"].value_counts().to_dict(),
        "avg_score": round(numeric_scores.mean(), 1),
        "top_ticker": df.loc[numeric_scores.idxmax(), "ticker"] if len(df) else None,
    }


def build_charts(df) -> list[tuple[str, bytes]]:
    """Returns [(content_id, png_bytes), ...]."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if df.empty:
        return []

    charts = []

    top20 = df.assign(score=df["score"].astype(float)).nlargest(20, "score")
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(top20["ticker"] + " " + top20["kind"], top20["score"])
    ax.invert_yaxis()
    ax.set_xlabel("Score")
    ax.set_title("Top 20 unusual moves today")
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    charts.append(("chart_top20", buf.getvalue()))

    fig, ax = plt.subplots(figsize=(6, 4))
    df["kind"].value_counts().plot(kind="bar", ax=ax)
    ax.set_title("Breakdown by kind")
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    charts.append(("chart_kind", buf.getvalue()))

    fig, ax = plt.subplots(figsize=(6, 4))
    df["score"].astype(float).plot(kind="hist", bins=10, ax=ax)
    ax.set_title("Score histogram")
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    charts.append(("chart_hist", buf.getvalue()))

    return charts


def _rows_html(df) -> str:
    if df.empty:
        return "<p>אין פעילות חריגה היום.</p>"

    top20 = df.assign(score=df["score"].astype(float)).nlargest(20, "score")
    rows_html = "".join(
        f"<tr><td>{r.ticker}</td><td>{r.asset_class}</td><td>{r.kind}</td>"
        f"<td>{float(r.score):.0f}</td><td>{r.expiry}</td><td>{r.strike}</td></tr>"
        for r in top20.itertuples()
    )
    return (
        '<table dir="rtl" border="1" cellpadding="4" cellspacing="0">'
        "<tr><th>טיקר</th><th>סוג נכס</th><th>סוג</th><th>ציון</th><th>פקיעה</th><th>סטרייק</th></tr>"
        f"{rows_html}</table>"
    )


def compose_email(df, summary: dict, charts: list[tuple[str, bytes]]) -> EmailMessage:
    msg = EmailMessage()
    today_str = datetime.now(timezone.utc).date().isoformat()
    msg["Subject"] = f"דוח יומי - תנועות חריגות ({today_str})"
    msg["From"] = os.environ["EMAIL_ADDRESS"]
    msg["To"] = os.environ["EMAIL_ADDRESS"]

    summary_html = (
        f"<p>סה\"כ התראות: {summary['total']} | ציון ממוצע: {summary['avg_score']} | "
        f"טיקר בולט: {summary.get('top_ticker') or '-'}</p>"
    )
    images_html = "".join(f'<p><img src="cid:{cid}"></p>' for cid, _ in charts)

    html_body = (
        f'<div dir="rtl" style="font-family:Arial,sans-serif;text-align:right">'
        f"<h2>דוח יומי - תנועות חריגות</h2>"
        f"{summary_html}"
        f"{_rows_html(df)}"
        f"{images_html}"
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
