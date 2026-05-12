#!/bin/bash
# install-all-hardened.sh — single-shot Phase 2 + Phase 3 of the
# hardened install. Handles everything the two-step flow does but
# from your main (sudo) user, without the manual `sudo -u ai-agent -i`
# context switch.
#
# Run as your main user with sudo:
#   sudo bash deploy/install-all-hardened.sh
#
# Or download + run without cloning first (since the repo is public):
#   curl -fsSL -o /tmp/naarad-install.sh \
#     https://raw.githubusercontent.com/MayankVachher/naarad/master/deploy/install-all-hardened.sh
#   sudo bash /tmp/naarad-install.sh
#
# Prerequisites:
#   - pi-hardening already applied (creates the AI user + /srv/<ai_user>).
#   - You have a BotFather token ready.
#   - Telegram client open so you can send your bot a message during
#     chat_id auto-detection.
#
# The script:
#   1. Prompts up-front for AI user, repo URL, LLM backend choice.
#   2. Installs nodejs+npm system-wide if you picked Copilot.
#   3. As $AI_USER: clones (or fast-forwards), installs uv, uv sync,
#      installs the LLM CLI, walks you through its auth, runs
#      configure.py, smoke-tests.
#   4. As root: substitutes the hardened systemd unit template,
#      daemon-reload, enable, restart.

set -euo pipefail

DEFAULT_REPO="${NAARAD_REPO_URL:-https://github.com/MayankVachher/naarad.git}"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  %s\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# --- Pre-flight ----------------------------------------------------------
[ "$EUID" -eq 0 ] || fail "Run with sudo: sudo bash $0"
command -v systemctl >/dev/null 2>&1 || fail "systemctl not found; this targets systemd Linux."
command -v git       >/dev/null 2>&1 || fail "git not found; sudo apt install -y git first."
command -v curl      >/dev/null 2>&1 || fail "curl not found; sudo apt install -y curl first."

# Grab the user's terminal back in case stdin is a pipe (curl|bash).
[ -t 0 ] || exec < /dev/tty

# --- Prompts -------------------------------------------------------------
bold "Naarad hardened install — full flow"
echo

read -p "  AI agent username [ai-agent]: " AI_USER
AI_USER="${AI_USER:-ai-agent}"
id "$AI_USER" &>/dev/null || fail "User '$AI_USER' does not exist — run pi-hardening first."

AI_HOME=$(getent passwd "$AI_USER" | cut -d: -f6)
[ -d "$AI_HOME" ] || fail "$AI_HOME does not exist."
INSTALL_DIR="/srv/$AI_USER/naarad"
[ -d "/srv/$AI_USER" ] || fail "/srv/$AI_USER does not exist — pi-hardening step 9 creates this."

read -p "  Repo URL [$DEFAULT_REPO]: " REPO_URL
REPO_URL="${REPO_URL:-$DEFAULT_REPO}"

echo
echo "  LLM backend choices:"
echo "    1) copilot — GitHub Copilot CLI (installs nodejs + npm system-wide)"
echo "    2) claude  — Anthropic Claude Code CLI"
echo "    3) none    — skip the LLM; bot uses deterministic plain renderer"
read -p "  Choose [1]: " CHOICE
case "${CHOICE:-1}" in
    1) BACKEND=copilot; LLM_CONFIG_DIR="$AI_HOME/.config/copilot" ;;
    2) BACKEND=claude;  LLM_CONFIG_DIR="$AI_HOME/.claude" ;;
    3) BACKEND=none;    LLM_CONFIG_DIR="$AI_HOME/.config/copilot" ;;  # unused, placeholder
    *) fail "Invalid choice '$CHOICE'." ;;
esac

echo
info "AI user:        $AI_USER  (home $AI_HOME)"
info "Install dir:    $INSTALL_DIR"
info "Repo:           $REPO_URL"
info "LLM backend:    $BACKEND"
echo
read -p "  Proceed? (Y/n) " -n 1 -r ANS; echo
[[ "${ANS:-Y}" =~ ^[Nn]$ ]] && fail "Aborted by user."

# --- Helper: run a block as the AI user with the right PATH --------------
# Don't use `sudo -i` here: sudo's login-shell mode joins multi-line `-c`
# arguments with spaces, which breaks multi-line bash blocks (the first
# `then` lands on the same line as previous statements and bash chokes).
# Instead use plain `-H` (set HOME from the target user's passwd entry)
# and a wrapper that exports PATH ourselves before running the supplied
# command. stdin/stdout/stderr still pass through to the user's terminal
# so the device-code auth flows and configure.py prompts work.
run_as_ai() {
    sudo -u "$AI_USER" -H bash -c "
export PATH=\"\$HOME/.local/bin:\$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin\"
$1
"
}

# --- Optional: install nodejs system-wide (needs sudo) -------------------
if [ "$BACKEND" = "copilot" ]; then
    if ! command -v node >/dev/null 2>&1; then
        bold "Installing nodejs + npm (system-wide)"
        apt-get update -qq
        apt-get install -y -qq nodejs npm
    else
        info "node already present ($(node --version))"
    fi
fi

# --- Phase 2.1: clone + uv + deps ---------------------------------------
bold "[2.1] clone + uv + uv sync (as $AI_USER)"
run_as_ai "
    set -euo pipefail
    cd /srv/$AI_USER
    if [ ! -d naarad ]; then
        git clone '$REPO_URL' naarad
    else
        cd naarad && git pull --ff-only && cd ..
    fi
    cd naarad
    if [ ! -x \$HOME/.local/bin/uv ] && ! command -v uv >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    \$HOME/.local/bin/uv sync
"

# --- Phase 2.2: install LLM CLI binary ----------------------------------
case "$BACKEND" in
    copilot)
        bold "[2.2] install Copilot CLI (as $AI_USER, no sudo)"
        run_as_ai "
            set -euo pipefail
            if [ ! -x \$HOME/.npm-global/bin/copilot ] && ! command -v copilot >/dev/null 2>&1; then
                mkdir -p \$HOME/.npm-global
                npm config set prefix \$HOME/.npm-global
                grep -q 'npm-global' \$HOME/.bashrc 2>/dev/null || \
                    echo 'export PATH=\$HOME/.npm-global/bin:\$PATH' >> \$HOME/.bashrc
                export PATH=\$HOME/.npm-global/bin:\$PATH
                npm install -g @githubnext/github-copilot-cli
            fi
        "
        ;;
    claude)
        bold "[2.2] install Claude Code CLI (as $AI_USER)"
        run_as_ai "
            set -euo pipefail
            if [ ! -x \$HOME/.local/bin/claude ] && ! command -v claude >/dev/null 2>&1; then
                curl -fsSL https://claude.ai/install.sh | sh
            fi
        "
        ;;
    none)
        info "[2.2] skipped (backend = none)"
        ;;
esac

# --- Phase 2.3: interactive LLM CLI auth --------------------------------
case "$BACKEND" in
    copilot)
        bold "[2.3] Copilot device-code auth"
        info "Follow the URL printed by the CLI; sign in; paste the code; press Enter."
        run_as_ai "PATH=\$HOME/.npm-global/bin:\$HOME/.local/bin:\$PATH copilot auth login"
        ;;
    claude)
        bold "[2.3] Claude device-code auth"
        info "Follow the URL printed by the CLI; sign in; paste the code; press Enter."
        run_as_ai "PATH=\$HOME/.local/bin:\$PATH claude login"
        ;;
esac

# --- Phase 2.4: configure naarad (interactive) --------------------------
bold "[2.4] configure.py — token + chat_id detection"
info "Paste your BotFather token when asked, then send your bot any message."
run_as_ai "cd $INSTALL_DIR && PATH=\$HOME/.local/bin:\$PATH uv run python deploy/configure.py"

# --- Phase 2.5: smoke test ---------------------------------------------
bold "[2.5] smoke-test (as $AI_USER, 8s)"
LOG=$(mktemp)
trap 'rm -f "$LOG"' EXIT
run_as_ai "cd $INSTALL_DIR && timeout 8 \$HOME/.local/bin/uv run python -m naarad.bot" >"$LOG" 2>&1 || true
if grep -q "startup validation passed" "$LOG"; then
    info "passed (saw 'startup validation passed')"
else
    printf '\n--- naarad output ---\n' >&2
    cat "$LOG" >&2
    fail "smoke test did not see 'startup validation passed'."
fi

# --- Phase 3: substitute + install systemd unit -------------------------
bold "[3] systemd unit"
TEMPLATE="$INSTALL_DIR/deploy/naarad-hardened.service.template"
SERVICE_PATH="/etc/systemd/system/naarad.service"
[ -f "$TEMPLATE" ] || fail "Template missing at $TEMPLATE."

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

# --- Status + tips ------------------------------------------------------
echo
systemctl status naarad --no-pager || true
echo
echo "Logs:"
echo "  journalctl -u naarad -f"
echo "  tail -f $INSTALL_DIR/logs/naarad.log"
echo
echo "Future updates (no full reinstall needed):"
echo "  sudo -u $AI_USER -i bash -c 'cd $INSTALL_DIR && git pull && \$HOME/.local/bin/uv sync'"
echo "  sudo systemctl restart naarad"
