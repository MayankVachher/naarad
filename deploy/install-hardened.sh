#!/bin/bash
# Naarad hardened installer for systemd-based Linux running pi-hardening.
#
# Symmetric with deploy/install.sh, but for the case where naarad runs
# as a sandboxed user (e.g. ai-agent from pi-hardening) rather than as
# the user running the script. Prompts for the four placeholders the
# hardened unit template needs, smoke-tests as the AI user, and
# installs the systemd unit.
#
# Run as your main (sudo) user from the install directory:
#   cd /srv/ai-agent/naarad
#   sudo bash deploy/install-hardened.sh
#
# DO NOT run from inside an ai-agent shell — it has no sudo by design.

set -euo pipefail

TEMPLATE="deploy/naarad-hardened.service.template"
SERVICE_PATH="/etc/systemd/system/naarad.service"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  %s\n' "$1"; }
warn() { printf '\033[33m  %s\033[0m\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# --- Helper: end-to-end verification with a code round-trip --------------
# Generates a short code, sends it via the bot to your Telegram chat,
# prompts you to type it back, deletes on success. Skippable via
# NAARAD_SKIP_VERIFY=1.
verify_round_trip() {
    local install_dir="$1"
    local config="$install_dir/config.json"
    [ -f "$config" ] || fail "config.json missing at $config"

    if [ "${NAARAD_SKIP_VERIFY:-0}" = "1" ]; then
        info "(skipped via NAARAD_SKIP_VERIFY=1)"
        return 0
    fi

    local code token chat
    code=$(tr -dc 'A-HJ-NP-Z2-9' < /dev/urandom | head -c 6)
    code="${code:0:3}-${code:3:3}"

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
[ "$EUID" -eq 0 ] || fail "Run with sudo."
[ -f "$TEMPLATE" ] || fail "Template not found at $TEMPLATE — run from the install dir (typically /srv/ai-agent/naarad)."
command -v systemctl >/dev/null 2>&1 || fail "systemctl not found; this targets systemd-based Linux."

# --- 1/4 AI account ------------------------------------------------------
bold "1/4 AI account"
read -p "  AI agent username [ai-agent]: " AI_USER_INPUT
AI_USER="${AI_USER_INPUT:-ai-agent}"

id "$AI_USER" &>/dev/null || fail "User '$AI_USER' does not exist (create it via pi-hardening first)."

AI_HOME=$(getent passwd "$AI_USER" | cut -d: -f6)
[ -d "$AI_HOME" ] || fail "Home dir '$AI_HOME' does not exist."
info "user: $AI_USER"
info "home: $AI_HOME"

# --- 2/4 Install dir + ownership check -----------------------------------
bold "2/4 Install dir"
INSTALL_DIR="$(pwd)"
[ -f "$INSTALL_DIR/pyproject.toml" ] || fail "$INSTALL_DIR/pyproject.toml missing — wrong dir?"
OWNER=$(stat -c '%U' "$INSTALL_DIR")
[ "$OWNER" = "$AI_USER" ] || fail "$INSTALL_DIR is owned by '$OWNER', expected '$AI_USER'. Did Phase 1 (clone + uv sync + configure.py) run inside an '$AI_USER' shell?"
info "install: $INSTALL_DIR (owner $OWNER)"

CONFIG_JSON="$INSTALL_DIR/config.json"
[ -f "$CONFIG_JSON" ] || fail "$CONFIG_JSON missing — run 'uv run python deploy/configure.py' as $AI_USER first."

# --- 3/4 LLM backend ----------------------------------------------------
bold "3/4 LLM backend"
DETECTED_BACKEND=$(python3 -c "import json; print(json.load(open('$CONFIG_JSON')).get('llm',{}).get('backend','copilot'))" 2>/dev/null || echo "copilot")
read -p "  LLM backend (copilot|claude) [$DETECTED_BACKEND]: " BACKEND_INPUT
BACKEND="${BACKEND_INPUT:-$DETECTED_BACKEND}"

case "$BACKEND" in
    copilot) LLM_CONFIG_DIR="$AI_HOME/.config/copilot" ;;
    claude)  LLM_CONFIG_DIR="$AI_HOME/.claude" ;;
    *) fail "Unknown backend '$BACKEND' (use copilot or claude)." ;;
esac
info "backend: $BACKEND"
info "auth dir: $LLM_CONFIG_DIR (will be writable inside the sandbox for token refresh)"

# --- 4/4 Smoke-test (config validation, no Telegram) + verification -----
bold "4/4 smoke-test (config validation, no Telegram contact)"
LOG=$(mktemp)
trap 'rm -f "$LOG"' EXIT
# NAARAD_SMOKE_TEST=1 makes the bot exit after validate_startup — no
# polling, no welcome. Full path to uv since `sudo -u` resets PATH.
sudo -u "$AI_USER" bash -c \
    "cd '$INSTALL_DIR' && NAARAD_SMOKE_TEST=1 timeout 10 '$AI_HOME/.local/bin/uv' run python -m naarad.bot" \
    >"$LOG" 2>&1 || true
if grep -q "startup validation passed" "$LOG"; then
    info "passed"
else
    printf '\n--- naarad output ---\n' >&2
    cat "$LOG" >&2
    fail "smoke test did not see 'startup validation passed'. Check the output above."
fi

bold "verification (round-trip with Telegram)"
verify_round_trip "$INSTALL_DIR"

# --- Confirm + install --------------------------------------------------
echo
echo "About to write $SERVICE_PATH with:"
echo "  User=$AI_USER"
echo "  Group=$AI_USER"
echo "  WorkingDirectory=$INSTALL_DIR"
echo "  ReadWritePaths=$INSTALL_DIR $LLM_CONFIG_DIR"
read -p "  Proceed? (Y/n) " -n 1 -r
echo
[[ $REPLY =~ ^[Nn]$ ]] && fail "aborted by user"

sed \
    -e "s|@AI_USER@|$AI_USER|g" \
    -e "s|@AI_HOME@|$AI_HOME|g" \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    -e "s|@LLM_CONFIG_DIR@|$LLM_CONFIG_DIR|g" \
    "$TEMPLATE" > "$SERVICE_PATH"
chmod 644 "$SERVICE_PATH"
info "wrote $SERVICE_PATH"

systemctl daemon-reload
systemctl enable naarad >/dev/null
systemctl restart naarad
info "service (re)started"

echo
systemctl status naarad --no-pager || true
echo
echo "Logs:"
echo "  journalctl -u naarad -f"
echo "  tail -f $INSTALL_DIR/logs/naarad.log"
echo
echo "Update flow (as $AI_USER):"
echo "  cd $INSTALL_DIR && git pull && uv sync"
echo "Then as your main user:"
echo "  sudo systemctl restart naarad"
