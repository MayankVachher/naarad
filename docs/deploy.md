# Deploy on a Raspberry Pi

Fresh Raspberry Pi OS, Pi already on your network, Telegram bot already created via BotFather.

## Standard install

```bash
git clone <repo> ~/naarad
cd ~/naarad
./deploy/install.sh        # or ./deploy/install.fish if you use fish
```

The script is idempotent — re-run after editing config or pulling updates. It will:

1. install `uv` if missing,
2. `uv sync`,
3. run `deploy/configure.py` if there's no `config.json` yet — it asks for the BotFather token, auto-detects your `chat_id` by polling Telegram for a message you send to the bot, takes an optional EODHD key, and prompts for timezone (defaulting to `/etc/timezone` if available). Re-run later with `uv run python deploy/configure.py` to change any of these — existing values appear as defaults so Enter keeps them. Location lat/lon stays at the example values; edit `config.json` afterwards if you live outside Toronto.
4. smoke-test the bot (looks for "startup validation passed"),
5. `sed` the systemd unit template into `/etc/systemd/system/naarad.service`, then `daemon-reload` + `enable` + `restart`,
6. print status + log paths.

## Optional: LLM CLI

Install one *before* running the install script if you want LLM-written briefs and water reminders. See [llm.md](llm.md) for backends and auth.

Without either, the bot uses the deterministic plain renderer and hardcoded reminder tones.

## Logs

Two places by design: journald (`journalctl -u naarad`) and `logs/naarad.log` (rotating, 5 MB × 3) inside the install directory.

## Update / remove

```bash
git pull && uv sync && sudo systemctl restart naarad     # update
./deploy/uninstall.sh                                    # or .fish
```

The uninstaller stops, disables, and removes the systemd unit. Repo, config, DB, and logs are left in place.

## Hardened install (pi-hardening / ai-agent account)

Single command, no shell switching:

```bash
sudo bash deploy/install-all-hardened.sh
```

Prompts upfront for the AI user, repo URL, and LLM backend. Then runs the whole flow: clones into `/srv/<ai_user>/naarad`, installs `uv` + deps as the AI user, installs and auths Copilot or Claude CLI inside the sandbox, runs `configure.py` for your token + chat_id, smoke-tests, and writes the hardened systemd unit (`User=<ai_user>`, tight `ReadWritePaths`, sandbox preserved). The interactive bits (device-code LLM auth, chat_id detection by you sending a message) pass through to your terminal because each AI-account command runs via `sudo -u <ai_user> -H bash -c …`.

If you'd rather run the two phases manually (Phase 2 as the AI user, then Phase 3 to install the unit), `deploy/install-hardened.sh` does just Phase 3 — useful if you've already got the install dir set up and just want to (re)write the systemd unit.

## Related config

See [REFERENCE.md](../REFERENCE.md) for the full `config.json` schema.
