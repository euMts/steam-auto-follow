from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session_factory
from app.models import LogEntry, RuntimeState, Task, TaskStatus, utcnow
from app.services.websocket_manager import ws_manager
from app.utils.url_validation import sanitize_log_message


async def get_or_create_runtime(session: AsyncSession) -> RuntimeState:
    result = await session.execute(select(RuntimeState).limit(1))
    state = result.scalar_one_or_none()
    if state is None:
        state = RuntimeState()
        session.add(state)
        await session.commit()
        await session.refresh(state)
    return state


class QueueService:
    async def create_tasks(
        self,
        session: AsyncSession,
        urls: list[str],
        action_type: str,
        max_attempts: int,
    ) -> list[Task]:
        return await self.create_task_items(
            session,
            items=[(url, action_type) for url in urls],
            max_attempts=max_attempts,
        )

    async def create_task_items(
        self,
        session: AsyncSession,
        items: list[tuple[str, str]],
        max_attempts: int,
    ) -> list[Task]:
        result = await session.execute(select(func.coalesce(func.max(Task.position), 0)))
        position = int(result.scalar_one())
        tasks: list[Task] = []
        for url, action_type in items:
            position += 1
            task = Task(
                url=url,
                action_type=action_type,
                status=TaskStatus.PENDING.value,
                attempts=0,
                max_attempts=max_attempts,
                position=position,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(task)
            tasks.append(task)
        await session.commit()
        for task in tasks:
            await session.refresh(task)
        await ws_manager.broadcast("queue_updated", {"created": [t.id for t in tasks]})
        return tasks

    async def list_tasks(
        self,
        session: AsyncSession,
        *,
        status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        filters = []
        if status:
            filters.append(Task.status == status)

        count_stmt: Select[Any] = select(func.count(Task.id))
        # Dashboard: mais recentes primeiro (a fila de execução continua FIFO em claim_next)
        list_stmt: Select[Any] = select(Task).order_by(Task.id.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)

        total = int((await session.execute(count_stmt)).scalar_one())
        rows = (
            await session.execute(list_stmt.offset(offset).limit(limit))
        ).scalars().all()
        return list(rows), total

    async def get_task(self, session: AsyncSession, task_id: int) -> Task | None:
        return await session.get(Task, task_id)

    async def claim_next(self, session: AsyncSession) -> Task | None:
        runtime = await get_or_create_runtime(session)
        if runtime.queue_paused or runtime.manual_action_required:
            return None

        result = await session.execute(
            select(Task)
            .where(Task.status == TaskStatus.PENDING.value)
            .order_by(Task.position.asc(), Task.id.asc())
            .limit(1)
        )
        task = result.scalar_one_or_none()
        if not task:
            return None

        task.status = TaskStatus.RUNNING.value
        task.started_at = utcnow()
        task.updated_at = utcnow()
        task.current_step = "Preparando navegador"
        task.attempts += 1
        await session.commit()
        await session.refresh(task)
        await ws_manager.broadcast(
            "task_updated",
            {"id": task.id, "status": task.status, "step": task.current_step},
        )
        return task

    async def update_step(self, session: AsyncSession, task_id: int, step: str) -> None:
        task = await session.get(Task, task_id)
        if not task:
            return
        task.current_step = step
        task.updated_at = utcnow()
        await session.commit()
        await ws_manager.broadcast(
            "task_updated",
            {"id": task.id, "status": task.status, "step": step},
        )

    async def complete_task(
        self,
        session: AsyncSession,
        task_id: int,
        message: str,
    ) -> None:
        task = await session.get(Task, task_id)
        if not task:
            return
        task.status = TaskStatus.COMPLETED.value
        task.result_message = message
        task.last_error = None
        task.finished_at = utcnow()
        task.updated_at = utcnow()
        task.current_step = "Finalizando"
        await session.commit()
        await ws_manager.broadcast(
            "task_updated",
            {"id": task.id, "status": task.status, "message": message},
        )
        await ws_manager.broadcast("queue_updated", {})

    async def fail_task(
        self,
        session: AsyncSession,
        task_id: int,
        error: str,
        *,
        retry: bool,
        screenshot_path: str | None = None,
    ) -> None:
        task = await session.get(Task, task_id)
        if not task:
            return

        task.last_error = error
        task.updated_at = utcnow()
        if screenshot_path:
            task.screenshot_path = screenshot_path

        if retry and task.attempts < task.max_attempts:
            task.status = TaskStatus.PENDING.value
            task.started_at = None
            task.finished_at = None
            task.current_step = None
            task.result_message = None
        else:
            task.status = TaskStatus.FAILED.value
            task.finished_at = utcnow()
            task.current_step = "Erro"

        await session.commit()
        await ws_manager.broadcast(
            "task_updated",
            {"id": task.id, "status": task.status, "error": error},
        )
        await ws_manager.broadcast("queue_updated", {})

    async def cancel_task(self, session: AsyncSession, task_id: int) -> Task | None:
        task = await session.get(Task, task_id)
        if not task:
            return None
        if task.status in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value):
            return task
        task.status = TaskStatus.CANCELLED.value
        task.finished_at = utcnow()
        task.updated_at = utcnow()
        task.current_step = "Cancelada"
        await session.commit()
        await ws_manager.broadcast("task_updated", {"id": task.id, "status": task.status})
        await ws_manager.broadcast("queue_updated", {})
        return task

    async def retry_task(self, session: AsyncSession, task_id: int) -> Task | None:
        task = await session.get(Task, task_id)
        if not task:
            return None
        task.status = TaskStatus.PENDING.value
        task.finished_at = None
        task.started_at = None
        task.last_error = None
        task.result_message = None
        task.current_step = None
        task.screenshot_path = None
        task.updated_at = utcnow()
        await session.commit()
        await session.refresh(task)
        await ws_manager.broadcast("task_updated", {"id": task.id, "status": task.status})
        await ws_manager.broadcast("queue_updated", {})
        return task

    async def retry_failed(self, session: AsyncSession) -> int:
        result = await session.execute(
            update(Task)
            .where(Task.status == TaskStatus.FAILED.value)
            .values(
                status=TaskStatus.PENDING.value,
                finished_at=None,
                started_at=None,
                last_error=None,
                result_message=None,
                current_step=None,
                screenshot_path=None,
                updated_at=utcnow(),
            )
        )
        await session.commit()
        await ws_manager.broadcast("queue_updated", {})
        return result.rowcount or 0

    async def clear_completed(self, session: AsyncSession) -> int:
        result = await session.execute(
            delete(Task).where(
                Task.status.in_(
                    [TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value]
                )
            )
        )
        await session.commit()
        await ws_manager.broadcast("queue_updated", {})
        return result.rowcount or 0

    async def delete_task(self, session: AsyncSession, task_id: int) -> bool:
        task = await session.get(Task, task_id)
        if not task:
            return False
        await session.delete(task)
        await session.commit()
        await ws_manager.broadcast("queue_updated", {})
        return True

    async def stats(self, session: AsyncSession) -> dict[str, Any]:
        runtime = await get_or_create_runtime(session)
        counts: dict[str, int] = {}
        for status in TaskStatus:
            result = await session.execute(
                select(func.count(Task.id)).where(Task.status == status.value)
            )
            counts[status.value] = int(result.scalar_one())
        return {
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
            "paused": runtime.queue_paused,
            "manual_action_required": runtime.manual_action_required,
            "manual_action_message": runtime.manual_action_message,
        }

    async def set_paused(self, session: AsyncSession, paused: bool) -> RuntimeState:
        runtime = await get_or_create_runtime(session)
        runtime.queue_paused = paused
        if not paused:
            runtime.manual_action_required = False
            runtime.manual_action_message = None
            # Retomada manual limpa o cooldown (usuário confirma que já esperou)
            runtime.rate_limit_cooldown_until = None
        await session.commit()
        await session.refresh(runtime)
        await ws_manager.broadcast(
            "queue_updated",
            {"paused": paused, "manual_action_required": runtime.manual_action_required},
        )
        return runtime

    async def require_manual_action(self, session: AsyncSession, message: str) -> None:
        runtime = await get_or_create_runtime(session)
        runtime.queue_paused = True
        runtime.manual_action_required = True
        runtime.manual_action_message = message
        await session.commit()
        await ws_manager.broadcast(
            "queue_updated",
            {
                "paused": True,
                "manual_action_required": True,
                "manual_action_message": message,
            },
        )

    async def recover_interrupted(self, session: AsyncSession) -> int:
        """Ao reiniciar, tarefas `running` voltam para `pending`."""
        result = await session.execute(
            update(Task)
            .where(Task.status == TaskStatus.RUNNING.value)
            .values(
                status=TaskStatus.PENDING.value,
                current_step=None,
                started_at=None,
                updated_at=utcnow(),
                last_error="Interrompida pelo encerramento do servidor; reenfileirada",
            )
        )
        await session.commit()
        return result.rowcount or 0

    async def current_running(self, session: AsyncSession) -> Task | None:
        result = await session.execute(
            select(Task).where(Task.status == TaskStatus.RUNNING.value).limit(1)
        )
        return result.scalar_one_or_none()


queue_service = QueueService()


async def append_log(level: str, source: str, message: str) -> None:
    clean = sanitize_log_message(message)
    factory = get_session_factory()
    async with factory() as session:
        entry = LogEntry(level=level.upper(), source=source, message=clean, created_at=utcnow())
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        payload = {
            "id": entry.id,
            "level": entry.level,
            "source": entry.source,
            "message": entry.message,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }
    await ws_manager.broadcast("log", payload)


async def list_logs(session: AsyncSession, limit: int = 100) -> list[LogEntry]:
    result = await session.execute(
        select(LogEntry).order_by(LogEntry.id.desc()).limit(limit)
    )
    rows = list(result.scalars().all())
    rows.reverse()
    return rows
