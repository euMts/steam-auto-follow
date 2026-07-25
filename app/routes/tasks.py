from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import MessageOut, TaskCreate, TaskListOut, TaskOut
from app.services.queue_service import append_log, get_or_create_runtime, queue_service
from app.services.status_service import task_to_out
from app.services.task_worker import task_worker

router = APIRouter(tags=["tasks", "queue"])


@router.get("/api/tasks", response_model=TaskListOut)
async def list_tasks(
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> TaskListOut:
    items, total = await queue_service.list_tasks(
        session, status=status, limit=limit, offset=offset
    )
    return TaskListOut(items=[task_to_out(item) for item in items], total=total)


@router.post("/api/tasks", response_model=TaskListOut)
async def create_tasks(
    payload: TaskCreate,
    session: AsyncSession = Depends(get_db),
) -> TaskListOut:
    runtime = await get_or_create_runtime(session)
    urls = payload.parsed_urls()
    tasks = await queue_service.create_tasks(
        session,
        urls=urls,
        action_type=payload.action_type.value,
        max_attempts=runtime.max_attempts,
    )
    await append_log(
        "INFO",
        "queue",
        f"{len(tasks)} tarefa(s) adicionada(s) — processamento automático iniciado",
    )
    task_worker.wake()
    return TaskListOut(items=[task_to_out(item) for item in tasks], total=len(tasks))


@router.get("/api/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: int, session: AsyncSession = Depends(get_db)) -> TaskOut:
    task = await queue_service.get_task(session, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    return task_to_out(task)


@router.delete("/api/tasks/{task_id}", response_model=MessageOut)
async def delete_task(task_id: int, session: AsyncSession = Depends(get_db)) -> MessageOut:
    ok = await queue_service.delete_task(session, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    await append_log("INFO", "queue", f"Tarefa #{task_id} removida")
    return MessageOut(message="Tarefa removida")


@router.post("/api/tasks/{task_id}/retry", response_model=TaskOut)
async def retry_task(task_id: int, session: AsyncSession = Depends(get_db)) -> TaskOut:
    task = await queue_service.retry_task(session, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    await append_log("INFO", "queue", f"Tarefa #{task_id} reenfileirada")
    task_worker.wake()
    return task_to_out(task)


@router.post("/api/tasks/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(task_id: int, session: AsyncSession = Depends(get_db)) -> TaskOut:
    task = await queue_service.cancel_task(session, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    await append_log("INFO", "queue", f"Tarefa #{task_id} cancelada")
    return task_to_out(task)


@router.post("/api/queue/pause", response_model=MessageOut)
async def pause_queue(session: AsyncSession = Depends(get_db)) -> MessageOut:
    await queue_service.set_paused(session, True)
    await append_log("INFO", "queue", "Fila pausada")
    return MessageOut(message="Fila pausada")


@router.post("/api/queue/resume", response_model=MessageOut)
async def resume_queue(session: AsyncSession = Depends(get_db)) -> MessageOut:
    await queue_service.set_paused(session, False)
    await append_log("INFO", "queue", "Fila retomada")
    task_worker.wake()
    return MessageOut(message="Fila retomada")


@router.post("/api/queue/retry-failed", response_model=MessageOut)
async def retry_failed(session: AsyncSession = Depends(get_db)) -> MessageOut:
    count = await queue_service.retry_failed(session)
    await append_log("INFO", "queue", f"{count} tarefa(s) com falha reenfileirada(s)")
    task_worker.wake()
    return MessageOut(message=f"{count} tarefa(s) reenfileirada(s)")


@router.delete("/api/queue/completed", response_model=MessageOut)
async def clear_completed(session: AsyncSession = Depends(get_db)) -> MessageOut:
    count = await queue_service.clear_completed(session)
    await append_log("INFO", "queue", f"{count} tarefa(s) concluída(s)/cancelada(s) removida(s)")
    return MessageOut(message=f"{count} tarefa(s) removida(s)")
