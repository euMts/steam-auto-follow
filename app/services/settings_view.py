from __future__ import annotations

from app.config import get_settings
from app.models import RuntimeState
from app.schemas import SettingsOut
from app.services.rate_limit_guard import rate_limit_guard


def build_settings_out(runtime: RuntimeState) -> SettingsOut:
    settings = get_settings()
    pacing = rate_limit_guard.status_payload()
    return SettingsOut(
        min_task_interval_seconds=runtime.min_task_interval_seconds,
        navigation_timeout_ms=runtime.navigation_timeout_ms,
        element_timeout_ms=runtime.element_timeout_ms,
        max_attempts=runtime.max_attempts,
        jitter_seconds=getattr(runtime, "jitter_seconds", 8.0),
        action_settle_ms=getattr(runtime, "action_settle_ms", 2500),
        click_delay_ms_min=getattr(runtime, "click_delay_ms_min", 600),
        click_delay_ms_max=getattr(runtime, "click_delay_ms_max", 1800),
        max_actions_per_hour=getattr(runtime, "max_actions_per_hour", 25),
        cooldown_after_rate_limit_seconds=getattr(
            runtime, "cooldown_after_rate_limit_seconds", 300.0
        ),
        auth_recheck_every_n_tasks=getattr(runtime, "auth_recheck_every_n_tasks", 5),
        rate_limit_cooldown_until=getattr(runtime, "rate_limit_cooldown_until", None),
        adaptive_multiplier=pacing["adaptive_multiplier"],
        actions_last_hour=pacing["actions_last_hour"],
        playwright_headless=settings.playwright_headless,
        app_host=settings.app_host,
        app_port=settings.app_port,
        steam_base_url=settings.steam_base_url,
    )
