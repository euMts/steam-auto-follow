from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.manager import browser_manager
from app.browser.steam_session import steam_session
from app.config import get_settings
from app.models import AuthStatus
from app.schemas import (
    AuthStatusOut,
    BrowserStatus,
    CookieStatus,
    CurrentTaskOut,
    DashboardStatus,
    LogOut,
    QueueStats,
    SettingsOut,
    TaskOut,
)
from app.services.queue_service import get_or_create_runtime, list_logs, queue_service
from app.services.task_worker import task_worker


def task_to_out(task) -> TaskOut:
    data = TaskOut.model_validate(task)
    data.has_screenshot = bool(task.screenshot_path)
    return data


async def build_dashboard_status(session: AsyncSession) -> DashboardStatus:
    settings = get_settings()
    runtime = await get_or_create_runtime(session)
    await browser_manager.refresh_state()
    cookie_status = CookieStatus(**(await steam_session.cookie_status(session)))
    stats = await queue_service.stats(session)
    logs = await list_logs(session, limit=50)
    running = await queue_service.current_running(session)

    auth_status = steam_session.auth_status
    if not cookie_status.configured and auth_status == AuthStatus.NOT_VERIFIED:
        auth_status = AuthStatus.COOKIES_MISSING

    current = CurrentTaskOut()
    if running:
        current = CurrentTaskOut(
            id=running.id,
            url=running.url,
            action_type=running.action_type,
            current_step=running.current_step,
            started_at=running.started_at,
            attempts=running.attempts,
            status_message=running.result_message or running.last_error,
        )
    elif task_worker.current_task_id:
        task = await queue_service.get_task(session, task_worker.current_task_id)
        if task:
            current = CurrentTaskOut(
                id=task.id,
                url=task.url,
                action_type=task.action_type,
                current_step=task.current_step,
                started_at=task.started_at,
                attempts=task.attempts,
                status_message=task.result_message or task.last_error,
            )

    return DashboardStatus(
        browser=BrowserStatus(**browser_manager.get_status_dict()),
        authentication=AuthStatusOut(
            status=auth_status,
            account_name=steam_session.account_name,
            checked_at=steam_session.checked_at or runtime.auth_checked_at,
            cookies=cookie_status,
        ),
        queue=QueueStats(**stats),
        current_task=current,
        recent_logs=[LogOut.model_validate(item) for item in logs],
        settings=SettingsOut(
            min_task_interval_seconds=runtime.min_task_interval_seconds,
            navigation_timeout_ms=runtime.navigation_timeout_ms,
            element_timeout_ms=runtime.element_timeout_ms,
            max_attempts=runtime.max_attempts,
            playwright_headless=settings.playwright_headless,
            app_host=settings.app_host,
            app_port=settings.app_port,
            steam_base_url=settings.steam_base_url,
        ),
        last_action=browser_manager.state.last_action or runtime.last_action,
        current_url=browser_manager.state.current_url or runtime.current_url,
    )
