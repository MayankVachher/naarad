#!/bin/bash
# Naarad installer for systemd-based Linux (e.g. Raspberry Pi OS).
# Idempotent — safe to re-run after editing config or pulling updates.
# Must be run from the repo root.

set -euo pipefail

INSTALL_DIR="$(pwd)"
SERVICE_PATH="/etc/systemd/system/naarad.service"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  %s\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

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

# --- 4. Smoke test --------------------------------------------------------
bold "4/6 smoke-test"
LOG=$(mktemp)
trap 'rm -f "$LOG"' EXIT
# Bot runs forever; let it spend a few seconds reaching validate_startup,
# then look for the success line and kill it. timeout returns 124 on its
# own kill, which set -e would otherwise abort on.
timeout 8 uv run python -m naarad.bot >"$LOG" 2>&1 || true
if grep -q "startup validation passed" "$LOG"; then
    info "passed (saw 'startup validation passed')"
else
    printf '\n--- naarad output ---\n' >&2
    cat "$LOG" >&2
    fail "smoke test did not see 'startup validation passed'. Fix the issue and re-run."
fi

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
