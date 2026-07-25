from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import BASE_DIR, SCREENSHOTS_DIR
from app.database import get_db
from app.schemas import DashboardStatus
from app.services.queue_service import list_logs
from app.services.status_service import build_dashboard_status
from app.services.websocket_manager import ws_manager

router = APIRouter(tags=["dashboard"])
INDEX_HTML = BASE_DIR / "app" / "templates" / "index.html"


@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


@router.get("/api/status", response_model=DashboardStatus)
async def api_status(session: AsyncSession = Depends(get_db)) -> DashboardStatus:
    return await build_dashboard_status(session)


@router.get("/api/logs")
async def api_logs(limit: int = 100, session: AsyncSession = Depends(get_db)):
    logs = await list_logs(session, limit=min(max(limit, 1), 500))
    return [
        {
            "id": item.id,
            "level": item.level,
            "source": item.source,
            "message": item.message,
            "created_at": item.created_at,
        }
        for item in logs
    ]


@router.get("/api/screenshots/{filename}")
async def get_screenshot(filename: str):
    safe = Path(filename).name
    path = SCREENSHOTS_DIR / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot não encontrado")
    return FileResponse(path, media_type="image/png")


@router.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception:
        await ws_manager.disconnect(websocket)
