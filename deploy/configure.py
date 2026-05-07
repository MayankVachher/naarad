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
import os
import re
import sys
import tempfile
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


# Update keys that carry a "chat" object whose id we should consider.
# /start commands arrive as plain ``message`` so that's the common case,
# but a user may already have edited a message or sent something to a
# linked channel; covering the variants keeps us from silently ignoring
# a real update during the 30s window.
_CHAT_BEARING_KEYS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
)


def _extract_chat_ids(updates: list[dict]) -> list[int]:
    chat_ids: list[int] = []
    for update in updates:
        for key in _CHAT_BEARING_KEYS:
            payload = update.get(key)
            if not payload:
                continue
            cid = payload.get("chat", {}).get("id")
            if isinstance(cid, int) and cid not in chat_ids:
                chat_ids.append(cid)
    return chat_ids


def _get_updates(base: str, params: dict | None = None) -> list[dict]:
    try:
        resp = httpx.get(f"{base}/getUpdates", params=params or {}, timeout=10)
    except Exception as exc:
        sys.exit(f"  ✗ Telegram API error: {exc}")
    if resp.status_code == 401:
        sys.exit("  ✗ Telegram rejected the token (401). Re-check what BotFather gave you.")
    if not (200 <= resp.status_code < 300):
        sys.exit(f"  ✗ Telegram returned HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except Exception as exc:
        sys.exit(f"  ✗ Telegram returned non-JSON: {exc}")
    if not data.get("ok"):
        sys.exit(f"  ✗ Telegram replied: {data}")
    return data.get("result") or []


def _pick_index(n: int) -> int:
    while True:
        raw = prompt("Index").strip()
        try:
            idx = int(raw)
        except ValueError:
            print(f"  ✗ Not a number; type 0..{n - 1}.")
            continue
        if not (0 <= idx < n):
            print(f"  ✗ Out of range; type 0..{n - 1}.")
            continue
        return idx


def fetch_chat_id(token: str) -> int:
    base = f"https://api.telegram.org/bot{token}"

    # Anchor on the current end of the queue so we only react to messages
    # sent AFTER setup starts. Without this, a bot that's been used before
    # would surface a stale chat_id from an old conversation.
    initial = _get_updates(base, {"offset": -1})
    last_id = initial[-1]["update_id"] if initial else 0

    print()
    print("Send any message to your bot from the chat you want it to use.")
    print("(If you've never messaged it: open the bot in Telegram and tap")
    print(" Start, or just send 'hello'.)")
    print()
    print(f"Polling Telegram for the next {POLL_ATTEMPTS * POLL_INTERVAL_S}s…")

    for _ in range(POLL_ATTEMPTS):
        updates = _get_updates(base, {"offset": last_id + 1, "timeout": 0})
        chat_ids = _extract_chat_ids(updates)
        if chat_ids:
            if len(chat_ids) == 1:
                return chat_ids[0]
            print("Multiple chats detected; pick one:")
            for i, cid in enumerate(chat_ids):
                print(f"  [{i}] {cid}")
            return chat_ids[_pick_index(len(chat_ids))]
        time.sleep(POLL_INTERVAL_S)

    sys.exit("  ✗ Timed out. Send the bot a message and re-run.")


def _write_chmod600(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` with mode 0600 — atomically and
    without ever exposing the file world-readable.

    ``Path.write_text`` would create the file using the process umask
    (usually 0644) and only later tighten via chmod, leaving a window
    where a Telegram bot token sits world-readable on disk.
    ``tempfile.mkstemp`` creates the tempfile already mode 0600 (per its
    docs: readable/writable only by the creating user), and
    ``os.replace`` is atomic and preserves the source inode's mode, so
    the destination is never world-readable on creation or overwrite.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".configtmp.")
    tmp_path = Path(tmp_name)
    # ``fdopen`` takes ownership of fd and closes it on context exit,
    # whether or not the body raises. Doing this before any other
    # operation prevents the fd from leaking if something between
    # mkstemp and fdopen failed.
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


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

    _write_chmod600(CONFIG, json.dumps(base, indent=2) + "\n")
    print(f"\n✓ Wrote {CONFIG} (chmod 600)")
    print("  Edit later to change timezone, location, schedules, or water intervals.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
