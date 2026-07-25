from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RuntimeState, utcnow
from app.services.queue_service import append_log, get_or_create_runtime
from app.services.websocket_manager import ws_manager


class RateLimitGuard:
    """Pacing conservador para reduzir chance de rate limit da Steam."""

    def __init__(self) -> None:
        self._actions_timestamps: list[datetime] = []
        self._cookies_applied_session = False
        self._auth_ok_session = False
        self._tasks_since_auth_check = 0
        self._adaptive_multiplier = 1.0
        self._consecutive_successes = 0

    def mark_cookies_applied(self) -> None:
        self._cookies_applied_session = True

    def needs_cookie_apply(self) -> bool:
        return not self._cookies_applied_session

    def needs_auth_check(self, every_n_tasks: int = 5) -> bool:
        if not self._auth_ok_session:
            return True
        return self._tasks_since_auth_check >= max(1, every_n_tasks)

    def mark_auth_ok(self) -> None:
        self._auth_ok_session = True
        self._tasks_since_auth_check = 0

    def invalidate_session_cache(self) -> None:
        self._cookies_applied_session = False
        self._auth_ok_session = False
        self._tasks_since_auth_check = 0

    def note_task_started(self) -> None:
        self._tasks_since_auth_check += 1
        self._actions_timestamps.append(datetime.now(timezone.utc))
        # Mantém só a última hora
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        self._actions_timestamps = [t for t in self._actions_timestamps if t >= cutoff]

    def note_success(self) -> None:
        self._consecutive_successes += 1
        # Decai o multiplicador lentamente após sucessos
        if self._consecutive_successes >= 3 and self._adaptive_multiplier > 1.0:
            self._adaptive_multiplier = max(1.0, round(self._adaptive_multiplier * 0.9, 2))
            self._consecutive_successes = 0

    async def note_rate_limit(self, session: AsyncSession) -> None:
        self._consecutive_successes = 0
        self._adaptive_multiplier = min(4.0, round(self._adaptive_multiplier * 1.75, 2))
        self.invalidate_session_cache()

        runtime = await get_or_create_runtime(session)
        cooldown = max(60.0, runtime.cooldown_after_rate_limit_seconds)
        runtime.rate_limit_cooldown_until = utcnow() + timedelta(seconds=cooldown)
        # Empurra intervalo base para cima temporariamente no runtime efetivo via multiplier
        if runtime.min_task_interval_seconds < 20:
            runtime.min_task_interval_seconds = min(60.0, runtime.min_task_interval_seconds + 5)
        await session.commit()
        await append_log(
            "WARNING",
            "pacing",
            f"Rate limit: cooldown {cooldown:.0f}s, multiplicador adaptativo={self._adaptive_multiplier:.2f}",
        )
        await ws_manager.broadcast(
            "queue_updated",
            {
                "rate_limit_cooldown_until": runtime.rate_limit_cooldown_until.isoformat()
                if runtime.rate_limit_cooldown_until
                else None,
                "adaptive_multiplier": self._adaptive_multiplier,
            },
        )

    async def assert_can_run(self, session: AsyncSession) -> None:
        runtime = await get_or_create_runtime(session)
        now = utcnow()

        if runtime.rate_limit_cooldown_until and runtime.rate_limit_cooldown_until > now:
            remaining = (runtime.rate_limit_cooldown_until - now).total_seconds()
            raise RuntimeError(
                f"Cooldownoldown anti-rate-limit ativo: aguarde mais {remaining:.0f}s"
            )

        cutoff = now - timedelta(hours=1)
        recent = [t for t in self._actions_timestamps if t >= cutoff]
        self._actions_timestamps = recent
        limit = max(1, int(runtime.max_actions_per_hour))
        if len(recent) >= limit:
            raise RuntimeError(
                f"Limite de {limit} ações/hora atingido — aguarde antes de continuar"
            )

    def compute_wait_seconds(self, runtime: RuntimeState) -> float:
        base = max(1.0, float(runtime.min_task_interval_seconds))
        jitter_max = max(0.0, float(runtime.jitter_seconds))
        jitter = random.uniform(0.0, jitter_max) if jitter_max > 0 else 0.0
        return (base + jitter) * self._adaptive_multiplier

    async def human_pause(
        self,
        *,
        min_ms: int = 400,
        max_ms: int = 1200,
        reason: str | None = None,
    ) -> None:
        delay = random.randint(max(50, min_ms), max(min_ms, max_ms)) / 1000.0
        if reason:
            await append_log("INFO", "pacing", f"{reason} (~{delay:.1f}s)")
        await asyncio.sleep(delay)

    async def settle_after_navigation(self, runtime: RuntimeState) -> None:
        base = max(500, int(runtime.action_settle_ms))
        await self.human_pause(
            min_ms=base,
            max_ms=base + int(runtime.jitter_seconds * 400),
            reason="Aguardando página estabilizar",
        )

    async def pause_before_click(self, runtime: RuntimeState) -> None:
        await self.human_pause(
            min_ms=max(300, int(runtime.click_delay_ms_min)),
            max_ms=max(int(runtime.click_delay_ms_min), int(runtime.click_delay_ms_max)),
            reason="Pausa antes do clique",
        )

    async def pause_between_subactions(self, runtime: RuntimeState) -> None:
        # Entre wishlist e follow, etc.
        base = max(1500, int(runtime.action_settle_ms))
        await self.human_pause(
            min_ms=base,
            max_ms=base + 2500,
            reason="Pausa entre subações",
        )

    @property
    def adaptive_multiplier(self) -> float:
        return self._adaptive_multiplier

    def status_payload(self) -> dict:
        return {
            "adaptive_multiplier": self._adaptive_multiplier,
            "actions_last_hour": len(self._actions_timestamps),
            "cookies_cached": self._cookies_applied_session,
            "auth_cached": self._auth_ok_session,
        }


rate_limit_guard = RateLimitGuard()
