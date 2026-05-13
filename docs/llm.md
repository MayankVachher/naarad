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

## Related config

See [REFERENCE.md](../REFERENCE.md) — the `llm.*` rows.
