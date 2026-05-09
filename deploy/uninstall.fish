#!/usr/bin/env fish
# Naarad uninstaller: stop + disable + remove the systemd unit.
# Leaves the repo, config.json, state.db, and logs/ in place — remove
# those by hand if you want a fully clean reset.

set SERVICE_PATH /etc/systemd/system/naarad.service

function bold;  printf '\033[1m%s\033[0m\n' $argv; end
function info;  printf '  %s\n' $argv; end
function fail;  printf '\033[31m✗ %s\033[0m\n' $argv >&2; exit 1; end

command -q systemctl; or fail "systemctl not found."
command -q sudo; or fail "sudo not found."

bold "1/3 stopping + disabling"
if sudo systemctl is-enabled naarad >/dev/null 2>&1
    sudo systemctl disable --now naarad; or fail "disable failed."
    info "stopped + disabled"
else if sudo systemctl is-active naarad >/dev/null 2>&1
    sudo systemctl stop naarad; or fail "stop failed."
    info "stopped (was not enabled)"
else
    info "(not running)"
end

bold "2/3 removing unit file"
if test -f $SERVICE_PATH
    sudo rm $SERVICE_PATH; or fail "could not remove $SERVICE_PATH."
    sudo systemctl daemon-reload; or fail "daemon-reload failed."
    info "removed $SERVICE_PATH"
else
    info "(not installed)"
end

bold "3/3 done"
echo
echo "The repo, config.json, state.db, and logs/ are untouched."
echo "For a clean reset:"
echo "  rm -rf "(pwd)
