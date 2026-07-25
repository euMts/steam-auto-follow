from __future__ import annotations

import asyncio
from pathlib import Path

from app.browser.actions import ActionError, ActionErrorCode, run_action
from app.browser.manager import BrowserNotRunningError, browser_manager
from app.browser.steam_session import steam_session
from app.config import SCREENSHOTS_DIR, ensure_data_dirs
from app.database import get_session_factory
from app.models import AuthStatus
from app.services.queue_service import append_log, get_or_create_runtime, queue_service
from app.services.websocket_manager import ws_manager
from app.utils.crypto import CookieCryptoError


MANUAL_CODES = {
    ActionErrorCode.CAPTCHA,
    ActionErrorCode.STEAM_GUARD,
    ActionErrorCode.LOGIN_PAGE,
    ActionErrorCode.RATE_LIMIT,
    ActionErrorCode.MANUAL_REQUIRED,
}


class TaskWorker:
    def __init__(self) -> None:
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._current_task_id: int | None = None
        self._page_lock = asyncio.Lock()

    @property
    def current_task_id(self) -> int | None:
        return self._current_task_id

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run_loop(), name="task-worker")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=15)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        self._task = None

    async def _run_loop(self) -> None:
        await append_log("INFO", "worker", "Worker de tarefas iniciado")
        while not self._stopping:
            processed = await self._process_one()
            if processed:
                factory = get_session_factory()
                async with factory() as session:
                    runtime = await get_or_create_runtime(session)
                    interval = runtime.min_task_interval_seconds
                await append_log(
                    "INFO",
                    "worker",
                    f"Aguardando intervalo mínimo de {interval:.0f}s entre tarefas",
                )
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=interval)
                    self._wake.clear()
                except asyncio.TimeoutError:
                    pass
                continue

            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

        await append_log("INFO", "worker", "Worker de tarefas encerrado")

    async def _process_one(self) -> bool:
        factory = get_session_factory()
        async with factory() as session:
            task = await queue_service.claim_next(session)
            if not task:
                return False
            task_id = task.id
            url = task.url
            action_type = task.action_type
            attempt = task.attempts
            max_attempts = task.max_attempts

        self._current_task_id = task_id
        await append_log("INFO", "worker", f"Iniciando tarefa #{task_id} (tentativa {attempt}/{max_attempts})")
        await ws_manager.broadcast("browser_status", browser_manager.get_status_dict())

        try:
            async with self._page_lock:
                await self._execute_task(task_id, url, action_type)
        except Exception as exc:  # noqa: BLE001
            await append_log("ERROR", "worker", f"Erro inesperado na tarefa #{task_id}: {exc}")
            async with factory() as session:
                await queue_service.fail_task(
                    session,
                    task_id,
                    f"Erro inesperado: {exc}",
                    retry=True,
                )
        finally:
            self._current_task_id = None
            await ws_manager.broadcast("queue_updated", {})
        return True

    async def _execute_task(self, task_id: int, url: str, action_type: str) -> None:
        factory = get_session_factory()

        async def set_step(step: str) -> None:
            async with factory() as session:
                await queue_service.update_step(session, task_id, step)
            await append_log("INFO", "worker", f"Tarefa #{task_id}: {step}")

        try:
            await set_step("Preparando navegador")
            await browser_manager.ensure_open()

            async with factory() as session:
                cookies = await steam_session.get_cookies(session)
                if not cookies.configured:
                    raise ActionError(
                        ActionErrorCode.COOKIES_MISSING,
                        "Cookies da Steam não configurados",
                    )

            await set_step("Aplicando cookies")
            async with factory() as session:
                await steam_session.apply_cookies(session)

            await set_step("Verificando autenticação")
            async with factory() as session:
                auth = await steam_session.verify_session(session, reapply=False)
            await ws_manager.broadcast(
                "authentication_status",
                {
                    "status": auth.status.value,
                    "account_name": auth.account_name,
                    "detail": auth.detail,
                },
            )
            if auth.status != AuthStatus.AUTHENTICATED:
                raise ActionError(
                    ActionErrorCode.NOT_AUTHENTICATED,
                    auth.detail or "Sessão não autenticada",
                )

            result = await run_action(
                action_type,
                url,
                browser_manager,
                step_callback=set_step,
            )

            async with factory() as session:
                runtime = await get_or_create_runtime(session)
                runtime.last_action = result.message
                runtime.current_url = browser_manager.state.current_url
                await session.commit()

            browser_manager.state.last_action = result.message
            await append_log("SUCCESS", "action", f"Tarefa #{task_id}: {result.message}")
            async with factory() as session:
                await queue_service.complete_task(session, task_id, result.message)

        except ActionError as exc:
            await self._handle_action_error(task_id, exc)
        except CookieCryptoError as exc:
            await self._handle_action_error(
                task_id,
                ActionError(ActionErrorCode.COOKIES_MISSING, str(exc)),
            )
        except BrowserNotRunningError as exc:
            await self._handle_action_error(
                task_id,
                ActionError(ActionErrorCode.UNEXPECTED, str(exc), retryable=True),
            )

    async def _handle_action_error(self, task_id: int, exc: ActionError) -> None:
        factory = get_session_factory()
        await append_log("ERROR", "action", f"Tarefa #{task_id}: {exc.message}")

        screenshot_path = await self._capture_failure(task_id)

        manual = exc.code in MANUAL_CODES
        if manual:
            async with factory() as session:
                await queue_service.require_manual_action(session, exc.message)
                await queue_service.fail_task(
                    session,
                    task_id,
                    exc.message,
                    retry=False,
                    screenshot_path=screenshot_path,
                )
            await append_log(
                "WARNING",
                "worker",
                "Fila pausada: ação manual necessária — resolva no navegador e retome",
            )
            return

        retry = exc.retryable
        async with factory() as session:
            await queue_service.fail_task(
                session,
                task_id,
                exc.message,
                retry=retry,
                screenshot_path=screenshot_path,
            )

    async def _capture_failure(self, task_id: int) -> str | None:
        if not browser_manager.is_open:
            return None
        ensure_data_dirs()
        factory = get_session_factory()
        async with factory() as session:
            task = await queue_service.get_task(session, task_id)
            attempt = task.attempts if task else 1
        filename = f"task-{task_id}-attempt-{attempt}.png"
        path = SCREENSHOTS_DIR / filename
        try:
            await browser_manager.screenshot(str(path))
            await append_log("INFO", "browser", f"Screenshot salvo: {filename}")
            return str(Path("data") / "screenshots" / filename)
        except Exception as exc:  # noqa: BLE001
            await append_log("WARNING", "browser", f"Falha ao salvar screenshot: {exc}")
            return None


task_worker = TaskWorker()
