#!/usr/bin/env fish
# Naarad hardened installer (fish variant) for systemd-based Linux
# running pi-hardening.
#
# Symmetric with deploy/install.fish, but for the case where naarad
# runs as a sandboxed user (e.g. ai-agent from pi-hardening) rather
# than as the user running the script.
#
# Run as your main (sudo) user from the install directory:
#   cd /srv/ai-agent/naarad
#   sudo fish deploy/install-hardened.fish
#
# DO NOT run from inside an ai-agent shell — it has no sudo by design.

set TEMPLATE deploy/naarad-hardened.service.template
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

    set code (tr -dc 'A-HJ-NP-Z2-9' < /dev/urandom | head -c 6)
    set code (string sub -l 3 $code)"-"(string sub -s 4 -l 3 $code)

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
test (id -u) -eq 0; or fail "Run with sudo."
test -f $TEMPLATE; or fail "Template not found at $TEMPLATE — run from the install dir (typically /srv/ai-agent/naarad)."
command -q systemctl; or fail "systemctl not found; this targets systemd-based Linux."

# --- 1/4 AI account ------------------------------------------------------
bold "1/4 AI account"
read -P "  AI agent username [ai-agent]: " AI_USER_INPUT
if test -z "$AI_USER_INPUT"
    set AI_USER ai-agent
else
    set AI_USER $AI_USER_INPUT
end

id $AI_USER >/dev/null 2>&1; or fail "User '$AI_USER' does not exist (create it via pi-hardening first)."

set AI_HOME (getent passwd $AI_USER | cut -d: -f6)
test -d "$AI_HOME"; or fail "Home dir '$AI_HOME' does not exist."
info "user: $AI_USER"
info "home: $AI_HOME"

# --- 2/4 Install dir + ownership check -----------------------------------
bold "2/4 Install dir"
set INSTALL_DIR (pwd)
test -f $INSTALL_DIR/pyproject.toml; or fail "$INSTALL_DIR/pyproject.toml missing — wrong dir?"
set OWNER (stat -c '%U' $INSTALL_DIR)
test "$OWNER" = "$AI_USER"; or fail "$INSTALL_DIR is owned by '$OWNER', expected '$AI_USER'. Did Phase 1 (clone + uv sync + configure.py) run inside an '$AI_USER' shell?"
info "install: $INSTALL_DIR (owner $OWNER)"

set CONFIG_JSON $INSTALL_DIR/config.json
test -f $CONFIG_JSON; or fail "$CONFIG_JSON missing — run 'uv run python deploy/configure.py' as $AI_USER first."

# --- 3/4 LLM backend ----------------------------------------------------
bold "3/4 LLM backend"
set DETECTED_BACKEND (python3 -c "import json; print(json.load(open('$CONFIG_JSON')).get('llm',{}).get('backend','copilot'))" 2>/dev/null; or echo copilot)
read -P "  LLM backend (copilot|claude) [$DETECTED_BACKEND]: " BACKEND_INPUT
if test -z "$BACKEND_INPUT"
    set BACKEND $DETECTED_BACKEND
else
    set BACKEND $BACKEND_INPUT
end

switch $BACKEND
    case copilot
        set LLM_CONFIG_DIR $AI_HOME/.config/copilot
    case claude
        set LLM_CONFIG_DIR $AI_HOME/.claude
    case '*'
        fail "Unknown backend '$BACKEND' (use copilot or claude)."
end
info "backend: $BACKEND"
info "auth dir: $LLM_CONFIG_DIR (will be writable inside the sandbox for token refresh)"

# --- 4/4 Smoke-test (config validation, no Telegram) + verification -----
bold "4/4 smoke-test (config validation, no Telegram contact)"
set LOG (mktemp)
# NAARAD_SMOKE_TEST=1 makes the bot exit after validate_startup — no
# polling, no welcome. Full path to uv since `sudo -u` resets PATH.
sudo -u $AI_USER bash -c "cd '$INSTALL_DIR' && NAARAD_SMOKE_TEST=1 timeout 10 '$AI_HOME/.local/bin/uv' run python -m naarad.bot" >$LOG 2>&1
if grep -q "startup validation passed" $LOG
    info "passed"
    rm -f $LOG
else
    printf '\n--- naarad output ---\n' >&2
    cat $LOG >&2
    rm -f $LOG
    fail "smoke test did not see 'startup validation passed'. Check the output above."
end

bold "verification (round-trip with Telegram)"
verify_round_trip $INSTALL_DIR

# --- Confirm + install --------------------------------------------------
echo
echo "About to write $SERVICE_PATH with:"
echo "  User=$AI_USER"
echo "  Group=$AI_USER"
echo "  WorkingDirectory=$INSTALL_DIR"
echo "  ReadWritePaths=$INSTALL_DIR $LLM_CONFIG_DIR"
read -P "  Proceed? (Y/n) " CONFIRM
if test "$CONFIRM" = "n"; or test "$CONFIRM" = "N"
    fail "aborted by user"
end

sed \
    -e "s|@AI_USER@|$AI_USER|g" \
    -e "s|@AI_HOME@|$AI_HOME|g" \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    -e "s|@LLM_CONFIG_DIR@|$LLM_CONFIG_DIR|g" \
    $TEMPLATE > $SERVICE_PATH; or fail "could not write $SERVICE_PATH"
chmod 644 $SERVICE_PATH
info "wrote $SERVICE_PATH"

systemctl daemon-reload; or fail "daemon-reload failed."
systemctl enable naarad >/dev/null; or fail "enable failed."
systemctl restart naarad; or fail "restart failed."
info "service (re)started"

echo
systemctl status naarad --no-pager
echo
echo "Logs:"
echo "  journalctl -u naarad -f"
echo "  tail -f $INSTALL_DIR/logs/naarad.log"
echo
echo "Update flow (as $AI_USER):"
echo "  cd $INSTALL_DIR; and git pull; and uv sync"
echo "Then as your main user:"
echo "  sudo systemctl restart naarad"
