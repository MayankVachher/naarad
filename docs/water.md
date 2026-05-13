# Water reminders

The water reminder chain runs during waking hours and escalates in interval and tone if you ignore it.

## Daily flow

1. **06:00** — silent morning brief drops in the chat with a `[☀️ Start day]` button.
2. Tap **[Start day]**. The bot greets you and arms the first water reminder.
3. **~3 min later** the first reminder fires (configurable via `water.first_reminder_delay_minutes`). The grace assumes you're walking away to brush teeth. The very first reminder uses a gentle, time-agnostic opener; subsequent reminders escalate.
4. **Tap 💧 Drank water**, reply to the reminder with anything, or send `/water` to confirm. The reminder rewrites itself to `✅ Glass #N logged at HH:MM`, and the reply tells you your running glass count, your pace status (🎯 target hit / 🟢 on track / ⚠️ at risk / 🚨 behind by ~N glasses), and the absolute time of the next reminder.
5. **11:00 fallback** — if you haven't tapped Start by then (or were asleep when the brief fired), the chain auto-starts.

After `water.active_end` (default 21:00) the bot goes quiet until next morning's start. Same once you hit `water.daily_target_glasses` for the day — no more nudges until tomorrow.

## Pace-adjusted intervals

Reminders run on a curve (`water.intervals_minutes`, default `[120, 60, 30, 15, 5]`), but the base interval is tightened when you fall behind the day's drinking pace.

`multiplier = max(pace_floor, 1 - deficit / daily_target)`

- On or ahead of pace → no change.
- Behind by 1 glass of an 8-target day → multiplier ≈ 0.88.
- Behind by 4 → multiplier 0.5 (half interval).
- Behind by 6+ → floored at `water.pace_floor` (default 0.3, so a 120-min base becomes at most ~36 min).

Set `water.daily_target_glasses: 0` to disable pace adjustment entirely — the bot still counts glasses but stops tweaking cadences.

## Catch-up after a bot restart

If the bot was offline at 06:00 and comes up before `water.active_end`, a catch-up brief fires shortly after boot so the day still kicks off. After bedtime there's no catch-up — tomorrow's normal schedule handles it.

## /status

`/status` surfaces the live state grouped by domain. The water section shows:

- whether the day has started,
- glasses-today vs target with the same pace badge as the confirm reply,
- last drink (HH:MM + date),
- current escalation level,
- the next-reminder time, or an explicit Idle reason (`day not started`, `🎯 target hit — done for today`, `🌙 active hours ended`).

## Related config

See [REFERENCE.md](../REFERENCE.md) — the `water.*`, `morning.*`, and `timezone` rows.
