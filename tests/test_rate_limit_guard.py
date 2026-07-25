from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.rate_limit_guard import RateLimitGuard


def test_compute_wait_includes_multiplier():
    guard = RateLimitGuard()
    guard._adaptive_multiplier = 2.0

    class FakeRuntime:
        min_task_interval_seconds = 10.0
        jitter_seconds = 0.0

    wait = guard.compute_wait_seconds(FakeRuntime())  # type: ignore[arg-type]
    assert wait == 20.0


def test_hourly_cap_tracks_actions():
    guard = RateLimitGuard()
    for _ in range(3):
        guard.note_task_started()
    assert guard.status_payload()["actions_last_hour"] == 3


def test_success_decays_multiplier():
    guard = RateLimitGuard()
    guard._adaptive_multiplier = 2.0
    for _ in range(3):
        guard.note_success()
    assert guard.adaptive_multiplier < 2.0
