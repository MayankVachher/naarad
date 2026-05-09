#!/bin/bash
# Naarad uninstaller: stop + disable + remove the systemd unit.
# Leaves the repo, config.json, state.db, and logs/ in place — remove
# those by hand if you want a fully clean reset.
set -euo pipefail

SERVICE_PATH="/etc/systemd/system/naarad.service"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  %s\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

command -v systemctl >/dev/null 2>&1 || fail "systemctl not found."
command -v sudo >/dev/null 2>&1 || fail "sudo not found."

bold "1/3 stopping + disabling"
if sudo systemctl is-enabled naarad >/dev/null 2>&1; then
    sudo systemctl disable --now naarad
    info "stopped + disabled"
elif sudo systemctl is-active naarad >/dev/null 2>&1; then
    sudo systemctl stop naarad
    info "stopped (was not enabled)"
else
    info "(not running)"
fi

bold "2/3 removing unit file"
if [ -f "$SERVICE_PATH" ]; then
    sudo rm "$SERVICE_PATH"
    sudo systemctl daemon-reload
    info "removed $SERVICE_PATH"
else
    info "(not installed)"
fi

bold "3/3 done"
echo
echo "The repo, config.json, state.db, and logs/ are untouched."
echo "For a clean reset:"
echo "  rm -rf $(pwd)"
