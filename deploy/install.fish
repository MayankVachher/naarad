#!/usr/bin/env fish
# Naarad installer for systemd-based Linux (e.g. Raspberry Pi OS).
# Idempotent — safe to re-run after editing config or pulling updates.
# Must be run from the repo root.

set INSTALL_DIR (pwd)
set SERVICE_PATH /etc/systemd/system/naarad.service

function bold;  printf '\033[1m%s\033[0m\n' $argv; end
function info;  printf '  %s\n' $argv; end
function fail;  printf '\033[31m✗ %s\033[0m\n' $argv >&2; exit 1; end

# --- Sanity --------------------------------------------------------------
test -f pyproject.toml; or fail "Run this from the naarad repo root."
test -f deploy/naarad.service.template; or fail "deploy/naarad.service.template missing."
command -q systemctl; or fail "systemctl not found; this script targets systemd-based Linux."
command -q sudo; or fail "sudo not found."

# --- 1. uv ---------------------------------------------------------------
bold "1/6 uv"
if command -q uv
    info "already installed ("(uv --version)")"
else
    curl -LsSf https://astral.sh/uv/install.sh | sh; or fail "uv install failed."
    set -gx PATH $HOME/.local/bin $PATH
    info "installed ("(uv --version)")"
    info "(open a new shell or source ~/.config/fish/config.fish for uv in your interactive sessions)"
end

# --- 2. Dependencies -----------------------------------------------------
bold "2/6 Python deps (uv sync)"
uv sync; or fail "uv sync failed."

# --- 3. Config ------------------------------------------------------------
bold "3/6 config.json"
if not test -f config.json
    cp config.example.json config.json; or fail "could not create config.json"
    chmod 600 config.json
    info "created config.json from example"
    fail "edit config.json (token, chat_id, location, …), then re-run this script."
end
chmod 600 config.json
if grep -q PUT_BOTFATHER_TOKEN_HERE config.json
    fail "config.json still has the placeholder token. Edit it before continuing."
end
info "present and chmod 600"

# --- 4. Smoke test --------------------------------------------------------
bold "4/6 smoke-test"
set LOG (mktemp)
# Bot runs forever; let it spend a few seconds reaching validate_startup,
# then look for the success line and kill it. timeout exits non-zero on
# its own kill — fish doesn't auto-abort, so no "or true" needed.
timeout 8 uv run python -m naarad.bot >$LOG 2>&1
if grep -q "startup validation passed" $LOG
    info "passed (saw 'startup validation passed')"
    rm -f $LOG
else
    printf '\n--- naarad output ---\n' >&2
    cat $LOG >&2
    rm -f $LOG
    fail "smoke test did not see 'startup validation passed'. Fix the issue and re-run."
end

# --- 5. systemd unit ------------------------------------------------------
bold "5/6 systemd unit (sudo)"
sed \
    -e "s|@USER@|$USER|g" \
    -e "s|@HOME@|$HOME|g" \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    deploy/naarad.service.template | sudo tee $SERVICE_PATH >/dev/null; or fail "could not install unit file."
sudo systemctl daemon-reload; or fail "daemon-reload failed."
sudo systemctl enable naarad >/dev/null; or fail "enable failed."
sudo systemctl restart naarad; or fail "restart failed."
info "installed and (re)started"

# --- 6. Status ------------------------------------------------------------
bold "6/6 status"
sudo systemctl status naarad --no-pager
echo
echo "Logs:"
echo "  journalctl -u naarad -f"
echo "  tail -f $INSTALL_DIR/logs/naarad.log"
echo
echo "Update flow:"
echo "  git pull; and uv sync; and sudo systemctl restart naarad"
