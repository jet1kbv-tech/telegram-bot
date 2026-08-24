from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.app import build_app
from bot.config import NOTIFICATION_CHECK_INTERVAL, TRIP_REMINDER_CHECK_INTERVAL


def test_build_app_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST_TOKEN")

    app = build_app()

    assert app is not None


def test_notification_scheduler_cadences(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST_TOKEN")
    app = build_app()
    jobs = {job.name: job for job in app.job_queue.jobs()}

    assert jobs["proactive_trip_reminders_v2"].job.trigger.interval.total_seconds() == 15 * 60
    assert TRIP_REMINDER_CHECK_INTERVAL == 15 * 60
    assert jobs["afisha_notifications"].job.trigger.interval.total_seconds() == 60 * 60
    assert NOTIFICATION_CHECK_INTERVAL == 60 * 60
