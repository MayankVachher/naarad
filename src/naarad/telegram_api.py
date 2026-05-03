"""Tiny synchronous Telegram sendMessage wrapper used by cron jobs.

The bot itself uses python-telegram-bot's async client; cron scripts are
short-lived and don't need an event loop, so they hit the HTTP API directly.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"


def send_message(
    token: str,
    chat_id: int,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool = True,
    disable_notification: bool = False,
    reply_markup: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> dict:
    """POST sendMessage. Raises httpx.HTTPError on transport failure or non-2xx.

    Returns the Telegram Message object (the API's `result` field), which
    includes `message_id` so callers can persist it for later edits.
    """
    url = f"{_API_BASE}/bot{token}/sendMessage"
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
        "disable_notification": disable_notification,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    resp = httpx.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body}")
    return body["result"]
