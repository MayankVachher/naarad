# LLM backend

The daily brief and water reminder lines can be written by an LLM. The bot shells out to a CLI; no API client lives in-process.

## Backends

Pick one via `llm.backend` in `config.json`:

- **`"copilot"`** (default) — GitHub Copilot CLI. Sign in with `copilot auth login`. Override the binary path with `COPILOT_BIN`.
- **`"claude"`** — Anthropic [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) CLI. Sign in with `claude login` or set `ANTHROPIC_API_KEY`. Override the binary path with `CLAUDE_BIN`.

The chosen CLI must be on `PATH` and authenticated. Without one, set `llm.enabled: false` (or just don't install a CLI) — the bot falls back to:

- a deterministic **plain renderer** for the morning brief, using the same pre-fetched RSS / weather / sunrise / Wikipedia data (see `src/naarad/brief/`),
- a **hardcoded escalating tone table** for water reminders.

## Toggling at runtime

- `/llm on|off` — flip the live state. Persists across restarts (DB-backed flag in `settings`).
- `/llm test` — fire a one-shot prompt at the configured backend; reports ✓ + the model's line or ✗ + the failure reason.
- `/llm` (no args) — show current state.

The config floor (`llm.enabled: false`) overrides runtime: flipping `/llm on` is a no-op while the floor is off.

## First-boot verification

On the very first run with a fresh `state.db`, the bot sends a welcome message echoing your config and includes an "LLM check" line. A few seconds later that line updates with ✓ and a one-liner from the model, or ✗ + the reason. Misconfig surfaces immediately instead of tomorrow morning.

## Debugging Claude's agentic loop

By default the bot logs the invocation envelope (start, elapsed, stdout size) — enough to see if Claude worked, not enough to see *what* it did inside the loop. To capture the full agentic trace (every tool call, every intermediate response, every timing), set `NAARAD_LLM_DEBUG=1` and Claude writes a per-call file to `logs/llm-debug/<timestamp>-<label>-<pid>.log` inside the install dir.

Flip it on:

```bash
# Hardened install — drop-in override
sudo systemctl edit naarad
# (an editor opens; add the two lines below, save, exit)
[Service]
Environment="NAARAD_LLM_DEBUG=1"

sudo systemctl restart naarad
# Trigger a brief; tail the newest debug file
sudo -u <ai_user> ls -t /srv/<ai_user>/naarad/logs/llm-debug/ | head -1
```

Off by default — files are ~MB per call and rotation isn't automatic. Override the dir with `NAARAD_LLM_DEBUG_DIR=/tmp/claude-debug` if you don't want them under the install tree.

Copilot doesn't have an equivalent flag; the env var only affects the Claude backend.

## Related config

See [REFERENCE.md](../REFERENCE.md) — the `llm.*` rows.
