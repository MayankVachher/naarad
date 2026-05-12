#!/bin/bash
# uninstall-all-hardened.sh — undo what install-all-hardened.sh did.
#
# Run as your main user with sudo:
#   sudo bash deploy/uninstall-all-hardened.sh
# or, if the install dir is already gone, from anywhere with the script
# downloaded somewhere:
#   curl -fsSL -o /tmp/naarad-uninstall.sh \
#     https://raw.githubusercontent.com/MayankVachher/naarad/master/deploy/uninstall-all-hardened.sh
#   sudo bash /tmp/naarad-uninstall.sh
#
# Removes (always):
#   • systemd unit at /etc/systemd/system/naarad.service
#   • install dir at /srv/$AI_USER/naarad
#   • downloaded installer at /tmp/naarad-install.sh
#
# Removes (with explicit y/N prompt):
#   • uv binary + caches under the AI user's home
#   • AS limit revert in /etc/security/limits.d/$AI_USER.conf
#     (only offered if it's currently the 6 GB value set during install)
#
# Does NOT touch pi-hardening artifacts (the AI user account, iptables
# rules, sudo deny, sshd DenyUsers, the /srv/$AI_USER directory).
# Those have nothing to do with naarad — they came from pi-hardening
# and should outlive naarad's lifecycle.

set -euo pipefail

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  %s\n' "$1"; }
warn() { printf '\033[33m  %s\033[0m\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# --- Pre-flight ----------------------------------------------------------
[ "$EUID" -eq 0 ] || fail "Run with sudo: sudo bash $0"
command -v systemctl >/dev/null 2>&1 || fail "systemctl not found; this targets systemd Linux."
[ -t 0 ] || exec < /dev/tty   # let curl|bash still prompt interactively

# --- Prompts -------------------------------------------------------------
bold "Naarad hardened uninstall"
echo

read -p "  AI agent username [ai-agent]: " AI_USER
AI_USER="${AI_USER:-ai-agent}"

if ! id "$AI_USER" &>/dev/null; then
    warn "User '$AI_USER' does not exist on this system."
    warn "Continuing in case fragments remain (systemd unit, /tmp script)."
    AI_HOME=""
else
    AI_HOME=$(getent passwd "$AI_USER" | cut -d: -f6)
fi

INSTALL_DIR="/srv/$AI_USER/naarad"
SERVICE_PATH="/etc/systemd/system/naarad.service"

echo
echo "About to remove (always):"
echo "  • systemd unit:    $SERVICE_PATH"
echo "  • install dir:     $INSTALL_DIR"
echo "  • script copy:     /tmp/naarad-install.sh (if present)"
echo
echo "Optional steps will be prompted individually after."
echo
read -p "  Proceed? (y/N) " -n 1 -r ANS; echo
[[ ! "${ANS:-N}" =~ ^[Yy]$ ]] && fail "Aborted by user."

# --- 1. systemd unit -----------------------------------------------------
bold "[1/3] systemd unit"
if [ -f "$SERVICE_PATH" ] || systemctl list-unit-files naarad.service &>/dev/null; then
    systemctl stop naarad    2>/dev/null || true
    systemctl disable naarad 2>/dev/null || true
    rm -f "$SERVICE_PATH"
    systemctl daemon-reload
    info "stopped, disabled, removed $SERVICE_PATH"
else
    info "(unit not installed, skipping)"
fi

# --- 2. Install dir ------------------------------------------------------
bold "[2/3] install dir"
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    info "removed $INSTALL_DIR"
else
    info "($INSTALL_DIR not present, skipping)"
fi

# --- 3. /tmp script ------------------------------------------------------
bold "[3/3] downloaded installer"
if [ -f /tmp/naarad-install.sh ]; then
    rm -f /tmp/naarad-install.sh
    info "removed /tmp/naarad-install.sh"
else
    info "(not present, skipping)"
fi

# --- Optional: uv + caches from AI user's home --------------------------
if [ -n "$AI_HOME" ] && [ -d "$AI_HOME" ]; then
    UV_PRESENT=false
    sudo -u "$AI_USER" bash -c '[ -e "$HOME/.local/bin/uv" ] || [ -d "$HOME/.cache/uv" ] || [ -d "$HOME/.local/share/uv" ]' \
        && UV_PRESENT=true || true

    if [ "$UV_PRESENT" = true ]; then
        echo
        read -p "  Remove uv binary + caches from $AI_HOME? (y/N) " -n 1 -r UV_ANS; echo
        if [[ "${UV_ANS:-N}" =~ ^[Yy]$ ]]; then
            sudo -u "$AI_USER" bash -c 'rm -rf ~/.local/bin/uv ~/.local/bin/uvx ~/.cache/uv ~/.local/share/uv' || true
            info "removed uv + caches from $AI_HOME"
        else
            info "(kept uv in $AI_HOME)"
        fi
    fi
fi

# --- Optional: revert pi-hardening AS limit -----------------------------
LIMITS_FILE="/etc/security/limits.d/$AI_USER.conf"
if [ -f "$LIMITS_FILE" ] && grep -qE "^$AI_USER\s+hard\s+as\s+6291456" "$LIMITS_FILE"; then
    echo
    read -p "  Revert AS limit (6 GB → 2 GB original) in $LIMITS_FILE? (y/N) " -n 1 -r LIMIT_ANS; echo
    if [[ "${LIMIT_ANS:-N}" =~ ^[Yy]$ ]]; then
        sed -i "s/^$AI_USER\s\+hard\s\+as\s\+.*/$AI_USER    hard    as          2097152/" "$LIMITS_FILE"
        info "reverted AS limit to 2 GB"
    else
        info "(AS limit stays at current value)"
    fi
fi

# --- Summary -------------------------------------------------------------
echo
bold "Done."
echo
echo "What's NOT touched (out of scope — comes from pi-hardening):"
echo "  • the '$AI_USER' user account + /home/$AI_USER"
echo "  • iptables UID rules for $AI_USER"
echo "  • /etc/sudoers.d/$AI_USER-deny"
echo "  • DenyUsers $AI_USER in /etc/ssh/sshd_config"
echo "  • /srv/$AI_USER (parent of the install dir)"
echo
echo "Optional housekeeping outside this script:"
echo "  • Revoke or delete the bot token: Telegram → @BotFather → /revoke or /deletebot"
