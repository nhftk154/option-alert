"""One-way Telegram push. Deliberately kept thin/one-directional - a future
interactive-bot module (commands, polling/webhook) would sit alongside this
file without needing to touch it."""

import os

import requests

_TIMEOUT = 10


def send_telegram_message(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    resp = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        # requests' own HTTPError message drops the response body, which is
        # where Telegram actually explains *why* (e.g. "chat not found",
        # "can't parse entities") - surface it so failures are diagnosable
        # from CI logs alone.
        raise RuntimeError(f"Telegram sendMessage failed ({resp.status_code}): {resp.text}")
