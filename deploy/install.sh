#!/bin/bash
# Naarad installer for systemd-based Linux (e.g. Raspberry Pi OS).
# Idempotent — safe to re-run after editing config or pulling updates.
# Must be run from the repo root.

set -euo pipefail

INSTALL_DIR="$(pwd)"
SERVICE_PATH="/etc/systemd/system/naarad.service"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  %s\n' "$1"; }
warn() { printf '\033[33m  %s\033[0m\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# --- Helper: end-to-end verification with a code round-trip --------------
# Generates a short code, sends it via the bot to your Telegram chat,
# prompts you to type it back, deletes on success. Proves token + chat_id
# + bidirectional reach in one step. Skippable via NAARAD_SKIP_VERIFY=1.
verify_round_trip() {
    local install_dir="$1"
    local config="$install_dir/config.json"
    [ -f "$config" ] || fail "config.json missing at $config"

    if [ "${NAARAD_SKIP_VERIFY:-0}" = "1" ]; then
        info "(skipped via NAARAD_SKIP_VERIFY=1)"
        return 0
    fi

    local code token chat
    # `tr ... | head -c N` is a SIGPIPE trap under set -o pipefail —
    # head closes the pipe, tr exits 141, script aborts silently.
    # python's secrets module sidesteps the issue.
    code=$(python3 -c "
import secrets
chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
c = ''.join(secrets.choice(chars) for _ in range(6))
print(f'{c[:3]}-{c[3:]}')
")

    token=$(python3 -c "import json; print(json.load(open('$config'))['telegram']['token'])")
    chat=$(python3 -c "import json; print(json.load(open('$config'))['telegram']['chat_id'])")

    info "Sending code to your bot's chat..."
    local resp
    resp=$(curl -fsS "https://api.telegram.org/bot${token}/sendMessage" \
        --data-urlencode "chat_id=${chat}" \
        --data-urlencode "text=🔐 Naarad install verification — type this in your terminal: ${code}") \
        || fail "Telegram sendMessage failed. Check the token, chat_id, and network."

    local msg_id
    msg_id=$(python3 -c "import sys, json; print(json.load(sys.stdin)['result']['message_id'])" <<<"$resp")

    echo
    info "Open Telegram → your bot's chat to see the code."
    local attempt entered
    for attempt in 1 2 3; do
        read -p "  Code: " entered
        if [ "$entered" = "$code" ]; then
            curl -fsS "https://api.telegram.org/bot${token}/deleteMessage" \
                --data-urlencode "chat_id=${chat}" \
                --data-urlencode "message_id=${msg_id}" >/dev/null 2>&1 || true
            info "✓ verified"
            return 0
        fi
        warn "mismatch — $((3 - attempt)) attempt(s) left"
    done
    fail "Verification failed after 3 attempts. The message stays in chat so you can see what was sent."
}

# --- Sanity --------------------------------------------------------------
[ -f pyproject.toml ] || fail "Run this from the naarad repo root."
[ -f deploy/naarad.service.template ] || fail "deploy/naarad.service.template missing."
command -v systemctl >/dev/null 2>&1 || fail "systemctl not found; this script targets systemd-based Linux."
command -v sudo >/dev/null 2>&1 || fail "sudo not found."

# --- 1. uv ---------------------------------------------------------------
bold "1/6 uv"
if command -v uv >/dev/null 2>&1; then
    info "already installed ($(uv --version))"
else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Surface uv's default install path for the rest of this script.
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    info "installed ($(uv --version))"
    info "(open a new shell or 'source ~/.bashrc' for uv in your interactive sessions)"
fi

# --- 2. Dependencies -----------------------------------------------------
bold "2/6 Python deps (uv sync)"
uv sync

# --- 3. Config ------------------------------------------------------------
bold "3/6 config.json"
if [ ! -f config.json ]; then
    info "no config.json yet — running interactive setup"
    uv run python deploy/configure.py
    [ -f config.json ] || fail "config.json wasn't created; aborting."
fi
chmod 600 config.json
if grep -q PUT_BOTFATHER_TOKEN_HERE config.json; then
    fail "config.json still has the placeholder token. Run 'uv run python deploy/configure.py' or edit it by hand."
fi
info "present and chmod 600"

# --- 4a. Smoke test (config validation only, no Telegram) ---------------
bold "4/6 smoke-test (config validation, no Telegram contact)"
LOG=$(mktemp)
trap 'rm -f "$LOG"' EXIT
# NAARAD_SMOKE_TEST=1 makes the bot exit cleanly after validate_startup,
# so no welcome / polling happens. timeout is just a safety bound;
# normal exit is ~2s.
NAARAD_SMOKE_TEST=1 timeout 10 uv run python -m naarad.bot >"$LOG" 2>&1 || true
if grep -q "startup validation passed" "$LOG"; then
    info "passed"
else
    printf '\n--- naarad output ---\n' >&2
    cat "$LOG" >&2
    fail "smoke test did not see 'startup validation passed'. Fix the issue and re-run."
fi

# --- 4b. Round-trip verification ----------------------------------------
bold "verification (round-trip with Telegram)"
verify_round_trip "$(pwd)"

# --- 5. systemd unit ------------------------------------------------------
bold "5/6 systemd unit (sudo)"
sed \
    -e "s|@USER@|$USER|g" \
    -e "s|@HOME@|$HOME|g" \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    deploy/naarad.service.template | sudo tee "$SERVICE_PATH" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable naarad >/dev/null
sudo systemctl restart naarad
info "installed and (re)started"

# --- 6. Status ------------------------------------------------------------
bold "6/6 status"
sudo systemctl status naarad --no-pager || true
echo
echo "Logs:"
echo "  journalctl -u naarad -f"
echo "  tail -f $INSTALL_DIR/logs/naarad.log"
echo
echo "Update flow:"
echo "  git pull && uv sync && sudo systemctl restart naarad"
