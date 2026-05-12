#!/usr/bin/env fish
# Naarad installer for systemd-based Linux (e.g. Raspberry Pi OS).
# Idempotent — safe to re-run after editing config or pulling updates.
# Must be run from the repo root.

set INSTALL_DIR (pwd)
set SERVICE_PATH /etc/systemd/system/naarad.service

function bold;  printf '\033[1m%s\033[0m\n' $argv; end
function info;  printf '  %s\n' $argv; end
function warn;  printf '\033[33m  %s\033[0m\n' $argv; end
function fail;  printf '\033[31m✗ %s\033[0m\n' $argv >&2; exit 1; end

# --- Helper: end-to-end verification with a code round-trip --------------
# Generates a code, sends via the bot, prompts user to type back, deletes
# on success. Skippable via NAARAD_SKIP_VERIFY=1.
function verify_round_trip --argument-names install_dir
    set config $install_dir/config.json
    test -f $config; or fail "config.json missing at $config"

    if test "$NAARAD_SKIP_VERIFY" = "1"
        info "(skipped via NAARAD_SKIP_VERIFY=1)"
        return 0
    end

    # Use python's secrets module — tr|head triggers SIGPIPE that
    # bash's pipefail catches silently. Fish doesn't have pipefail but
    # we want symmetric behavior across both shells.
    set code (python3 -c "
import secrets
chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
c = ''.join(secrets.choice(chars) for _ in range(6))
print(f'{c[:3]}-{c[3:]}')
")

    set token (python3 -c "import json; print(json.load(open('$config'))['telegram']['token'])")
    set chat (python3 -c "import json; print(json.load(open('$config'))['telegram']['chat_id'])")

    info "Sending code to your bot's chat..."
    set resp (curl -fsS "https://api.telegram.org/bot$token/sendMessage" \
        --data-urlencode "chat_id=$chat" \
        --data-urlencode "text=🔐 Naarad install verification — type this in your terminal: $code")
    or fail "Telegram sendMessage failed. Check the token, chat_id, and network."

    set msg_id (echo $resp | python3 -c "import sys, json; print(json.load(sys.stdin)['result']['message_id'])")

    echo
    info "Open Telegram → your bot's chat to see the code."
    for attempt in 1 2 3
        read -P "  Code: " entered
        if test "$entered" = "$code"
            curl -fsS "https://api.telegram.org/bot$token/deleteMessage" \
                --data-urlencode "chat_id=$chat" \
                --data-urlencode "message_id=$msg_id" >/dev/null 2>&1; or true
            info "✓ verified"
            return 0
        end
        warn "mismatch — "(math 3 - $attempt)" attempt(s) left"
    end
    fail "Verification failed after 3 attempts. The message stays in chat so you can see what was sent."
end

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
    info "no config.json yet — running interactive setup"
    uv run python deploy/configure.py; or fail "interactive setup failed."
    test -f config.json; or fail "config.json wasn't created; aborting."
end
chmod 600 config.json
if grep -q PUT_BOTFATHER_TOKEN_HERE config.json
    fail "config.json still has the placeholder token. Run 'uv run python deploy/configure.py' or edit it by hand."
end
info "present and chmod 600"

# --- 4a. Smoke test (config validation, no Telegram) --------------------
bold "4/6 smoke-test (config validation, no Telegram contact)"
set LOG (mktemp)
# NAARAD_SMOKE_TEST=1 makes the bot exit cleanly after validate_startup;
# no polling, no welcome. timeout 10 is just a safety bound.
NAARAD_SMOKE_TEST=1 timeout 10 uv run python -m naarad.bot >$LOG 2>&1
if grep -q "startup validation passed" $LOG
    info "passed"
    rm -f $LOG
else
    printf '\n--- naarad output ---\n' >&2
    cat $LOG >&2
    rm -f $LOG
    fail "smoke test did not see 'startup validation passed'. Fix the issue and re-run."
end

# --- 4b. Round-trip verification ----------------------------------------
bold "verification (round-trip with Telegram)"
verify_round_trip (pwd)

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
