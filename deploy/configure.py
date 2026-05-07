#!/usr/bin/env python3
"""Interactive bootstrap for naarad's config.json.

Run via:  uv run python deploy/configure.py

Prompts for the few things that can't be sensibly defaulted:
  - Telegram bot token (from BotFather)
  - Telegram chat_id — auto-detected by polling getUpdates after you
    send a message to the bot
  - EODHD API key (optional)

Everything else (timezone, location, schedules, water intervals…) is
seeded from config.example.json; edit config.json by hand afterwards
if you live somewhere other than Toronto.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.json"
CONFIG = ROOT / "config.json"

TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")

POLL_INTERVAL_S = 2
POLL_ATTEMPTS = 15  # 30s total


def prompt(label: str, default: str | None = None, required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        print("  (required)")


def prompt_token() -> str:
    while True:
        token = prompt("Telegram bot token (from BotFather)")
        if TOKEN_RE.match(token):
            return token
        print("  ✗ Token doesn't match expected shape (digits:secret).")


def fetch_chat_id(token: str) -> int:
    base = f"https://api.telegram.org/bot{token}"
    print()
    print("Send any message to your bot from the chat you want it to use.")
    print("(If you've never messaged it: open the bot in Telegram and tap")
    print(" Start, or just send 'hello'.)")
    print()
    print(f"Polling Telegram for the next {POLL_ATTEMPTS * POLL_INTERVAL_S}s…")

    for attempt in range(POLL_ATTEMPTS):
        try:
            resp = httpx.get(f"{base}/getUpdates", timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            sys.exit(f"  ✗ Telegram API error: {exc}")
        if not data.get("ok"):
            sys.exit(f"  ✗ Telegram replied: {data}")

        chat_ids: list[int] = []
        for update in data.get("result") or []:
            cid = (update.get("message") or {}).get("chat", {}).get("id")
            if isinstance(cid, int) and cid not in chat_ids:
                chat_ids.append(cid)

        if chat_ids:
            if len(chat_ids) == 1:
                return chat_ids[0]
            print("Multiple chats detected; pick one:")
            for i, cid in enumerate(chat_ids):
                print(f"  [{i}] {cid}")
            idx = int(prompt("Index"))
            return chat_ids[idx]

        time.sleep(POLL_INTERVAL_S)

    sys.exit("  ✗ Timed out. Send the bot a message and re-run.")


def main() -> int:
    if not EXAMPLE.exists():
        sys.exit(f"  ✗ {EXAMPLE} missing — run from the repo root.")

    if CONFIG.exists():
        ans = prompt("config.json already exists. Overwrite?", default="n").lower()
        if ans not in ("y", "yes"):
            print("Aborted; existing config.json untouched.")
            return 0

    base = json.loads(EXAMPLE.read_text())

    print("Bootstrapping config.json. Other fields default to Toronto;")
    print("edit config.json by hand afterwards if you live elsewhere.\n")

    token = prompt_token()
    base["telegram"]["token"] = token

    chat_id = fetch_chat_id(token)
    print(f"  ✓ chat_id = {chat_id}")
    base["telegram"]["chat_id"] = chat_id

    print()
    eodhd = prompt(
        "EODHD API key (optional — enables /quote + market briefs; Enter to skip)",
        required=False,
    )
    base["eodhd"]["api_key"] = eodhd

    CONFIG.write_text(json.dumps(base, indent=2) + "\n")
    CONFIG.chmod(0o600)
    print(f"\n✓ Wrote {CONFIG} (chmod 600)")
    print("  Edit later to change timezone, location, schedules, or water intervals.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
