"""Scheduler glue tests: confirm/reminder/start_day flows with mocks.

We don't spin up python-telegram-bot's full Application here. Instead we
exercise `run_loop`, `start_day`, and `confirm_drink` against a fake
Application that captures sends and scheduled jobs.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from naarad import db
from naarad.config import (
    BriefConfig,
    Config,
    EodhdConfig,
    MorningConfig,
    SchedulesConfig,
    TelegramConfig,
)
from naarad.config import (
    WaterConfig as ConfigWater,
)
from naarad.llm.runner import LLMResult
from naarad.water import scheduler as water_scheduler
from naarad.water.scheduler import water_config_from

TZ = ZoneInfo("America/Toronto")


def make_config(db_path: Path, *, daily_target: int = 0) -> Config:
    """Default disables pace adjustment so timing-sensitive tests can
    assert exact next_due values. Pace-specific tests pass daily_target=N.
    """
    return Config(
        telegram=TelegramConfig(token="x", chat_id=1),
        eodhd=EodhdConfig(api_key="x"),
        timezone="America/Toronto",
        water=ConfigWater(
            active_end="21:00",
            intervals_minutes=[120, 60, 30, 15, 5],
            daily_target_glasses=daily_target,
        ),
        brief=BriefConfig(),
        morning=MorningConfig(),
        tickers_default=[],
        schedules=SchedulesConfig(),
        db_path=str(db_path),
    )


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.stripped: list[int] = []  # message_ids whose buttons were removed
        self._next_msg_id = 1000

    async def send_message(self, *, chat_id, text, reply_markup=None):
        msg_id = self._next_msg_id
        self._next_msg_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, "message_id": msg_id})
        return SimpleNamespace(message_id=msg_id)

    async def edit_message_reply_markup(self, *, chat_id, message_id, reply_markup):
        # We only ever call this with reply_markup=None to strip the keyboard.
        assert reply_markup is None
        self.stripped.append(message_id)


class FakeJobQueue:
    def __init__(self) -> None:
        self.scheduled: list[tuple[str, datetime]] = []
        self.removed: list[str] = []

    def get_jobs_by_name(self, name):
        return []  # No persistence between schedule calls in this fake.

    def run_once(self, callback, *, when, name):
        self.scheduled.append((name, when))


class FakeApp:
    def __init__(self, config: Config) -> None:
        self.bot = FakeBot()
        self.job_queue = FakeJobQueue()
        self.bot_data = {
            "config": config,
            "water_cfg": water_config_from(config),
            "water_lock": asyncio.Lock(),
        }


@pytest.fixture(autouse=True)
def stub_llm_runner(monkeypatch):
    """Force the LLM runner to return a failed result so render() takes
    the fallback path (the hardcoded ``messages._TONES`` line).

    Otherwise these tests would shell out to the real ``copilot``/``claude``
    CLI on every reminder send (slow + brittle).
    """
    def _fail(backend, prompt, timeout, log_label):
        return LLMResult(ok=False, error_reason="stubbed-out")
    monkeypatch.setattr("naarad.llm.dispatch.run_llm", _fail)


@pytest.fixture
def app(tmp_path: Path):
    config = make_config(tmp_path / "test.db")
    db.init_db(config.db_path)
    return FakeApp(config)


@pytest.fixture
def freeze_now(monkeypatch):
    """Replace scheduler._now() with a controllable clock."""
    holder = {"now": datetime(2026, 5, 2, 10, 0, tzinfo=TZ)}

    def _fake_now(tz):
        return holder["now"].astimezone(tz)

    monkeypatch.setattr(water_scheduler, "_now", _fake_now)
    return holder


# ---------- Day not started: kickoff is a no-op ----------

@pytest.mark.asyncio
async def test_kickoff_when_day_not_started_is_idle(app, freeze_now):
    freeze_now["now"] = datetime(2026, 5, 2, 8, 30, tzinfo=TZ)
    await water_scheduler.kickoff(app)
    # No messages, no scheduled jobs.
    assert app.bot.sent == []
    assert app.job_queue.scheduled == []


@pytest.mark.asyncio
async def test_kickoff_when_yesterday_started_is_idle(app, freeze_now):
    """day_started_on=yesterday must not fire today."""
    cfg = app.bot_data["config"]
    db.update_water_state(
        cfg.db_path,
        day_started_on=date(2026, 5, 1),
        last_drink_at=datetime(2026, 5, 1, 18, 0, tzinfo=TZ),
        level=2,
    )
    freeze_now["now"] = datetime(2026, 5, 2, 9, 0, tzinfo=TZ)
    await water_scheduler.kickoff(app)
    assert app.bot.sent == []
    assert app.job_queue.scheduled == []


# ---------- start_day: marks today and fires first reminder ----------

@pytest.mark.asyncio
async def test_start_day_marks_today_and_schedules_grace(app, freeze_now):
    """Tap Start at 08:30 → no reminder fires immediately, water-loop is
    parked at 08:35 (default grace = 5 min). chain_started_at is persisted
    so a bot restart mid-grace doesn't reset the timer."""
    cfg = app.bot_data["config"]
    freeze_now["now"] = datetime(2026, 5, 2, 8, 30, tzinfo=TZ)
    await water_scheduler.start_day(app)

    state = db.get_water_state(cfg.db_path)
    assert state["day_started_on"] == date(2026, 5, 2)
    assert state["chain_started_at"] == datetime(2026, 5, 2, 8, 30, tzinfo=TZ)
    # Level still 0 — no reminder has fired yet.
    assert state["level"] == 0

    # Nothing sent yet (grace in progress).
    assert [m for m in app.bot.sent if "💧" in m["text"]] == []

    # Job parked at start + grace = 08:35.
    name, when = app.job_queue.scheduled[-1]
    assert name == water_scheduler.JOB_NAME
    assert when == datetime(2026, 5, 2, 8, 35, tzinfo=TZ)


@pytest.mark.asyncio
async def test_first_reminder_fires_after_grace_expires(app, freeze_now):
    """Advance time past the grace window and trip the loop — first
    reminder should now fire and level bump to 1."""
    cfg = app.bot_data["config"]
    freeze_now["now"] = datetime(2026, 5, 2, 8, 30, tzinfo=TZ)
    await water_scheduler.start_day(app)
    app.bot.sent.clear()
    app.job_queue.scheduled.clear()

    # Time advances past the 5-min grace.
    freeze_now["now"] = datetime(2026, 5, 2, 8, 35, tzinfo=TZ)
    await water_scheduler.run_loop(app)

    reminders = [m for m in app.bot.sent if "💧" in m["text"]]
    assert len(reminders) == 1
    state = db.get_water_state(cfg.db_path)
    assert state["level"] == 1
    # After level 0 fires, intervals[1] = 60min, so next due 09:35.
    name, when = app.job_queue.scheduled[-1]
    assert when == datetime(2026, 5, 2, 9, 35, tzinfo=TZ)


@pytest.mark.asyncio
async def test_start_day_idempotent_within_day(app, freeze_now):
    """Calling start_day twice on the same day must NOT reset the chain
    back to a fresh grace window — the second call sees
    day_started_on==today and just continues."""
    cfg = app.bot_data["config"]
    freeze_now["now"] = datetime(2026, 5, 2, 8, 30, tzinfo=TZ)
    await water_scheduler.start_day(app)
    # After the first start_day: still in grace, level 0.
    state_after_first = db.get_water_state(cfg.db_path)
    assert state_after_first["level"] == 0
    assert state_after_first["last_drink_at"] is None
    assert state_after_first["chain_started_at"] == datetime(2026, 5, 2, 8, 30, tzinfo=TZ)

    # User confirms a drink (skips the rest of the chain — level resets,
    # last_drink_at set).
    freeze_now["now"] = datetime(2026, 5, 2, 9, 0, tzinfo=TZ)
    await water_scheduler.confirm_drink(app)
    state_after_drink = db.get_water_state(cfg.db_path)
    assert state_after_drink["level"] == 0
    assert state_after_drink["last_drink_at"] == datetime(2026, 5, 2, 9, 0, tzinfo=TZ)

    # Spurious second start_day later in the morning — must NOT clobber.
    freeze_now["now"] = datetime(2026, 5, 2, 10, 0, tzinfo=TZ)
    await water_scheduler.start_day(app)
    state_after_second = db.get_water_state(cfg.db_path)
    # last_drink_at preserved, day_started_on still today.
    assert state_after_second["last_drink_at"] == datetime(2026, 5, 2, 9, 0, tzinfo=TZ)
    assert state_after_second["day_started_on"] == date(2026, 5, 2)


# ---------- confirm flow ----------

@pytest.mark.asyncio
async def test_confirm_drink_returns_new_glass_count(app, freeze_now):
    """confirm_drink is the canonical accessor handlers use to get the
    count for the user-visible response. It must return the post-confirm
    count atomically with the state mutation."""
    cfg = app.bot_data["config"]
    freeze_now["now"] = datetime(2026, 5, 2, 8, 30, tzinfo=TZ)
    await water_scheduler.start_day(app)

    freeze_now["now"] = datetime(2026, 5, 2, 9, 0, tzinfo=TZ)
    n1 = await water_scheduler.confirm_drink(app)
    assert n1 == 1
    assert db.get_water_state(cfg.db_path)["glasses_today"] == 1

    freeze_now["now"] = datetime(2026, 5, 2, 10, 0, tzinfo=TZ)
    n2 = await water_scheduler.confirm_drink(app)
    assert n2 == 2
    assert db.get_water_state(cfg.db_path)["glasses_today"] == 2


@pytest.mark.asyncio
async def test_confirm_resets_level_and_schedules_2h_out(app, freeze_now):
    cfg = app.bot_data["config"]
    # Day already started + first reminder fired.
    freeze_now["now"] = datetime(2026, 5, 2, 8, 30, tzinfo=TZ)
    await water_scheduler.start_day(app)
    app.bot.sent.clear()
    app.job_queue.scheduled.clear()

    freeze_now["now"] = datetime(2026, 5, 2, 10, 30, tzinfo=TZ)
    await water_scheduler.confirm_drink(app)

    state = db.get_water_state(cfg.db_path)
    assert state["last_drink_at"] == datetime(2026, 5, 2, 10, 30, tzinfo=TZ)
    assert state["level"] == 0
    name, when = app.job_queue.scheduled[-1]
    assert when == datetime(2026, 5, 2, 12, 30, tzinfo=TZ)


@pytest.mark.asyncio
async def test_confirm_before_day_start_is_silent(app, freeze_now):
    """A /water confirm before the day starts updates state but doesn't
    fire reminders — the next_action returns Idle.
    """
    cfg = app.bot_data["config"]
    freeze_now["now"] = datetime(2026, 5, 2, 7, 0, tzinfo=TZ)  # before brief / before tap
    await water_scheduler.confirm_drink(app)

    # Confirm went through (state updated)…
    state = db.get_water_state(cfg.db_path)
    assert state["last_drink_at"] == datetime(2026, 5, 2, 7, 0, tzinfo=TZ)
    # …but no reminder fired and nothing got scheduled.
    reminders = [m for m in app.bot.sent if "💧" in m["text"]]
    assert reminders == []
    assert app.job_queue.scheduled == []


# ---------- Recovery / stale callback semantics ----------

@pytest.mark.asyncio
async def test_overdue_reminder_at_startup_fires_once_at_current_level(app, freeze_now):
    """Bot was scheduled to fire at 12:00 but didn't (crash); restarts at 13:00.

    With the new design: day_started_on must be set already (from earlier today).
    """
    cfg = app.bot_data["config"]
    db.update_water_state(
        cfg.db_path,
        day_started_on=date(2026, 5, 2),
        last_drink_at=datetime(2026, 5, 2, 10, 0, tzinfo=TZ),
        level=0,
    )
    freeze_now["now"] = datetime(2026, 5, 2, 13, 0, tzinfo=TZ)

    await water_scheduler.kickoff(app)

    # Exactly one reminder sent at level 0.
    reminders = [m for m in app.bot.sent if "💧" in m["text"]]
    assert len(reminders) == 1
    assert reminders[0]["text"].startswith("💧 Time")
    # Level bumped to 1; next reminder scheduled at 13:00 + 60min = 14:00.
    state = db.get_water_state(cfg.db_path)
    assert state["level"] == 1
    name, when = app.job_queue.scheduled[-1]
    assert when == datetime(2026, 5, 2, 14, 0, tzinfo=TZ)


@pytest.mark.asyncio
async def test_stale_callback_after_confirm_is_a_noop(app, freeze_now):
    """Reminder job was scheduled for 12:00. User confirms at 11:59. Job fires at 12:00.

    The state has been reset, so no escalation message goes out.
    """
    cfg = app.bot_data["config"]
    db.update_water_state(
        cfg.db_path,
        day_started_on=date(2026, 5, 2),
        last_drink_at=datetime(2026, 5, 2, 10, 0, tzinfo=TZ),
        level=0,
    )
    freeze_now["now"] = datetime(2026, 5, 2, 11, 59, tzinfo=TZ)
    await water_scheduler.confirm_drink(app)
    app.bot.sent.clear()
    app.job_queue.scheduled.clear()

    freeze_now["now"] = datetime(2026, 5, 2, 12, 0, tzinfo=TZ)
    await water_scheduler.run_loop(app)

    # No reminder sent — confirm at 11:59 set last_drink_at, next due is 13:59.
    reminders = [m for m in app.bot.sent if "💧" in m["text"]]
    assert reminders == []
    name, when = app.job_queue.scheduled[-1]
    assert when == datetime(2026, 5, 2, 13, 59, tzinfo=TZ)


@pytest.mark.asyncio
async def test_after_active_end_is_idle(app, freeze_now):
    """After 21:00 on a started day, run_loop is silent."""
    cfg = app.bot_data["config"]
    db.update_water_state(
        cfg.db_path,
        day_started_on=date(2026, 5, 2),
        last_drink_at=datetime(2026, 5, 2, 18, 0, tzinfo=TZ),
        level=2,
    )
    freeze_now["now"] = datetime(2026, 5, 2, 22, 0, tzinfo=TZ)
    await water_scheduler.kickoff(app)
    assert app.bot.sent == []
    assert app.job_queue.scheduled == []


# ---------- First-of-day messaging ----------

@pytest.mark.asyncio
async def test_first_reminder_uses_first_of_day_fallback(app, freeze_now):
    """The very first reminder of the day uses FIRST_OF_DAY_MESSAGE,
    not the level-0 nudge — different opener after the morning routine."""
    freeze_now["now"] = datetime(2026, 5, 2, 8, 30, tzinfo=TZ)
    await water_scheduler.start_day(app)
    app.bot.sent.clear()

    # Trip the loop after grace.
    freeze_now["now"] = datetime(2026, 5, 2, 8, 35, tzinfo=TZ)
    await water_scheduler.run_loop(app)

    text = app.bot.sent[-1]["text"]
    # The hardcoded first-of-day fallback wins because the autouse
    # fixture stubs out the LLM with a failure result.
    assert text == "💧 Morning. First sip when you're ready."


@pytest.mark.asyncio
async def test_new_reminder_strips_button_off_previous(app, freeze_now):
    """Each new reminder strips the 💧 button off the previous one so
    only the latest reminder is tappable. The first reminder of the day
    has nothing to strip; the second strips the first's; the third
    strips the second's; etc."""
    cfg = app.bot_data["config"]
    freeze_now["now"] = datetime(2026, 5, 2, 8, 30, tzinfo=TZ)
    await water_scheduler.start_day(app)

    # First reminder fires after the 5-min grace.
    freeze_now["now"] = datetime(2026, 5, 2, 8, 35, tzinfo=TZ)
    await water_scheduler.run_loop(app)
    assert len(app.bot.sent) == 1
    first_msg_id = app.bot.sent[-1]["message_id"]
    # Nothing to strip — there's no previous reminder yet.
    assert app.bot.stripped == []

    # Second reminder fires after intervals[1] = 60min from the first.
    freeze_now["now"] = datetime(2026, 5, 2, 9, 35, tzinfo=TZ)
    await water_scheduler.run_loop(app)
    assert len(app.bot.sent) == 2
    second_msg_id = app.bot.sent[-1]["message_id"]
    # The first reminder's button got stripped before the second sent.
    assert app.bot.stripped == [first_msg_id]

    # Third reminder strips the second.
    freeze_now["now"] = datetime(2026, 5, 2, 10, 5, tzinfo=TZ)  # +intervals[2]=30
    await water_scheduler.run_loop(app)
    assert len(app.bot.sent) == 3
    assert app.bot.stripped == [first_msg_id, second_msg_id]

    # DB last_msg_id always points at the most recent.
    assert db.get_water_state(cfg.db_path)["last_msg_id"] == app.bot.sent[-1]["message_id"]


@pytest.mark.asyncio
async def test_subsequent_reminder_uses_regular_level_messaging(app, freeze_now):
    """After the first reminder fires, follow-up reminders use the
    regular escalation copy, not the first-of-day variant."""
    cfg = app.bot_data["config"]
    freeze_now["now"] = datetime(2026, 5, 2, 8, 30, tzinfo=TZ)
    await water_scheduler.start_day(app)

    # First reminder fires after grace.
    freeze_now["now"] = datetime(2026, 5, 2, 8, 35, tzinfo=TZ)
    await water_scheduler.run_loop(app)
    app.bot.sent.clear()

    # Now state.last_reminder_at is set; trip the loop again at the
    # next-due time. Should use regular level-1 messaging.
    state = db.get_water_state(cfg.db_path)
    assert state["level"] == 1
    freeze_now["now"] = datetime(2026, 5, 2, 9, 35, tzinfo=TZ)
    await water_scheduler.run_loop(app)

    text = app.bot.sent[-1]["text"]
    assert text != "💧 Morning. First sip when you're ready."
    assert "💧" in text


# ---------- Lock-drop semantics around render ----------

@pytest.mark.asyncio
async def test_confirm_during_render_discards_rendered_line(
    app, freeze_now, monkeypatch
):
    """If the user confirms while text is being rendered (lock released),
    the rendered line is discarded — no spurious reminder is sent and the
    confirm's anchor wins.
    """
    cfg = app.bot_data["config"]
    db.update_water_state(
        cfg.db_path,
        day_started_on=date(2026, 5, 2),
        last_drink_at=datetime(2026, 5, 2, 10, 0, tzinfo=TZ),
        level=0,
    )
    freeze_now["now"] = datetime(2026, 5, 2, 12, 0, tzinfo=TZ)  # reminder due

    # Simulate a slow render that confirms-mid-flight: when the renderer is
    # called, slip in a confirm before it returns.
    async def _slow_render(config, level, *, first_of_day=False):
        await water_scheduler.confirm_drink(app)
        return "💧 should-not-be-sent"

    monkeypatch.setattr(water_scheduler, "_render_reminder_text", _slow_render)

    app.bot.sent.clear()
    await water_scheduler.run_loop(app)

    # No reminder lines went out — the rendered text was discarded because
    # the recheck saw last_drink_at advanced by the confirm.
    assert "should-not-be-sent" not in [m["text"] for m in app.bot.sent]
    # And the inner run_loop triggered by confirm_drink must not have sent
    # anything either (post-confirm, next_action is Sleep, not Reminder).
    assert app.bot.sent == []
    # State reflects the confirm.
    state = db.get_water_state(cfg.db_path)
    assert state["last_drink_at"] == datetime(2026, 5, 2, 12, 0, tzinfo=TZ)
    assert state["level"] == 0
