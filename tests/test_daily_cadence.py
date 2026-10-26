"""Daily API deadlines must not be rounded up to quarter-hour polling ticks."""

import asyncio
from datetime import datetime, timedelta

import pytest

from src.manager import web
from src.manager.domain import UTC
from src.manager.settings import Settings
from src.manager.store import SQLiteStore


def test_daily_budget_wakes_between_five_minute_checks():
    now = datetime(2026, 10, 21, 13, tzinfo=UTC)
    deadline = now + timedelta(seconds=100)
    assert (
        web.poll_timeout(
            now, {"status": "waiting", "next_write_at": deadline.isoformat()}
        )
        == 100
    )


@pytest.mark.parametrize(
    "actuation",
    [
        None,
        {"status": "waiting"},
        {"status": "waiting", "next_write_at": "invalid"},
        {"status": "waiting", "next_write_at": "2026-10-21T13:01:00"},
        {"status": "waiting", "next_write_at": "2026-10-21T13:00:00+00:00"},
        {"status": "waiting", "next_write_at": "2026-10-21T12:59:59+00:00"},
        {"status": "blocked", "next_write_at": "2026-10-21T13:01:00+00:00"},
        {"status": "waiting", "next_write_at": "2026-10-22T13:01:00+00:00"},
    ],
)
def test_stale_or_unusable_budget_deadlines_do_not_spin_or_skip_monitoring(actuation):
    now = datetime(2026, 10, 21, 13, tzinfo=UTC)
    assert web.poll_timeout(now, actuation) == 300


def test_five_minute_check_aligns_to_clock_boundary():
    now = datetime(2026, 10, 21, 13, 3, 40, tzinfo=UTC)
    assert web.poll_timeout(now) == 80


def test_imminent_deadline_has_a_bounded_retry_interval():
    now = datetime(2026, 10, 21, 13, tzinfo=UTC)
    assert (
        web.poll_timeout(
            now,
            {
                "status": "waiting",
                "next_write_at": (now + timedelta(milliseconds=20)).isoformat(),
            },
        )
        == 1
    )


async def test_poll_loop_uses_persisted_daily_write_deadline(monkeypatch, tmp_path):
    now = datetime(2026, 10, 21, 13, tzinfo=UTC)
    store = SQLiteStore(tmp_path / "cadence.sqlite")
    observed = []

    class WaitingController:
        async def run_once(self):
            store.put(
                "actuation",
                {
                    "status": "waiting",
                    "next_write_at": (now + timedelta(seconds=100)).isoformat(),
                },
            )

    async def capture_wait(awaitable, timeout):
        observed.append(timeout)
        awaitable.close()
        raise asyncio.CancelledError

    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-only")
    monkeypatch.setattr(web, "utc_now", lambda: now)
    monkeypatch.setattr(web.asyncio, "wait_for", capture_wait)
    app = web.create_app(Settings(), store, poll=True, controller=WaitingController())
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
    assert observed == [100]
