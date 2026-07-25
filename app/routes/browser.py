from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.manager import browser_manager
from app.database import get_db
from app.schemas import BrowserStatus, MessageOut
from app.services.queue_service import append_log, queue_service
from app.services.websocket_manager import ws_manager

router = APIRouter(tags=["browser"])


@router.post("/api/browser/start", response_model=BrowserStatus)
async def start_browser() -> BrowserStatus:
    await browser_manager.start()
    await append_log("INFO", "browser", "Navegador iniciado")
    status = BrowserStatus(**browser_manager.get_status_dict())
    await ws_manager.broadcast("browser_status", status.model_dump())
    return status


@router.post("/api/browser/restart", response_model=BrowserStatus)
async def restart_browser() -> BrowserStatus:
    await browser_manager.restart()
    await append_log("INFO", "browser", "Navegador reiniciado")
    status = BrowserStatus(**browser_manager.get_status_dict())
    await ws_manager.broadcast("browser_status", status.model_dump())
    return status


@router.post("/api/browser/close", response_model=BrowserStatus)
async def close_browser() -> BrowserStatus:
    await browser_manager.close()
    await append_log("INFO", "browser", "Navegador fechado")
    status = BrowserStatus(**browser_manager.get_status_dict())
    await ws_manager.broadcast("browser_status", status.model_dump())
    return status


@router.post("/api/browser/open-task/{task_id}", response_model=MessageOut)
async def open_task_url(task_id: int, session: AsyncSession = Depends(get_db)) -> MessageOut:
    task = await queue_service.get_task(session, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    await browser_manager.ensure_open()
    await browser_manager.goto(task.url)
    await append_log("INFO", "browser", f"URL da tarefa #{task_id} aberta no navegador")
    await ws_manager.broadcast("browser_status", browser_manager.get_status_dict())
    return MessageOut(message=f"URL aberta: {task.url}")
