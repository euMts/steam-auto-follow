from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.steam_session import steam_session
from app.config import get_settings
from app.database import get_db
from app.schemas import (
    CookieInput,
    CookieStatus,
    MessageOut,
    SettingsOut,
    SettingsUpdate,
)
from app.services.queue_service import append_log, get_or_create_runtime
from app.services.task_worker import task_worker
from app.services.websocket_manager import ws_manager
from app.utils.crypto import CookieCryptoError

router = APIRouter(tags=["settings", "session"])


@router.get("/api/settings", response_model=SettingsOut)
async def get_app_settings(session: AsyncSession = Depends(get_db)) -> SettingsOut:
    settings = get_settings()
    runtime = await get_or_create_runtime(session)
    return SettingsOut(
        min_task_interval_seconds=runtime.min_task_interval_seconds,
        navigation_timeout_ms=runtime.navigation_timeout_ms,
        element_timeout_ms=runtime.element_timeout_ms,
        max_attempts=runtime.max_attempts,
        playwright_headless=settings.playwright_headless,
        app_host=settings.app_host,
        app_port=settings.app_port,
        steam_base_url=settings.steam_base_url,
    )


@router.put("/api/settings", response_model=SettingsOut)
async def update_app_settings(
    payload: SettingsUpdate,
    session: AsyncSession = Depends(get_db),
) -> SettingsOut:
    runtime = await get_or_create_runtime(session)
    if payload.min_task_interval_seconds is not None:
        runtime.min_task_interval_seconds = payload.min_task_interval_seconds
    if payload.navigation_timeout_ms is not None:
        runtime.navigation_timeout_ms = payload.navigation_timeout_ms
    if payload.element_timeout_ms is not None:
        runtime.element_timeout_ms = payload.element_timeout_ms
    if payload.max_attempts is not None:
        runtime.max_attempts = payload.max_attempts
    await session.commit()
    await append_log("INFO", "settings", "Configurações atualizadas")
    return await get_app_settings(session)


@router.post("/api/session/cookies", response_model=CookieStatus)
async def save_cookies(
    payload: CookieInput,
    session: AsyncSession = Depends(get_db),
) -> CookieStatus:
    try:
        await steam_session.save_cookies(
            session,
            store_steam_login_secure=payload.store.steam_login_secure,
            store_sessionid=payload.store.sessionid,
            community_steam_login_secure=payload.community.steam_login_secure,
            community_sessionid=payload.community.sessionid,
        )
    except CookieCryptoError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await append_log(
        "INFO",
        "session",
        "Cookies Store e Community salvos (valores ocultos, pares distintos)",
    )
    status = CookieStatus(**(await steam_session.cookie_status(session)))

    try:
        auth = await steam_session.verify_session(session)
        await append_log(
            "INFO",
            "session",
            f"Verificação automática: {auth.status.value}"
            + (f" ({auth.account_name})" if auth.account_name else ""),
        )
        await ws_manager.broadcast(
            "authentication_status",
            {"status": auth.status.value, "account_name": auth.account_name},
        )
    except Exception as exc:  # noqa: BLE001
        await append_log("WARNING", "session", f"Falha na verificação automática: {exc}")

    await ws_manager.broadcast("authentication_status", {"cookies": status.model_dump()})
    task_worker.wake()
    return status


@router.delete("/api/session/cookies", response_model=MessageOut)
async def delete_cookies(session: AsyncSession = Depends(get_db)) -> MessageOut:
    await steam_session.clear_cookies(session)
    await append_log("INFO", "session", "Cookies da Steam removidos")
    await ws_manager.broadcast("authentication_status", {"status": "cookies_missing"})
    return MessageOut(message="Cookies removidos")


@router.post("/api/session/verify")
async def verify_session(session: AsyncSession = Depends(get_db)):
    try:
        result = await steam_session.verify_session(session)
    except CookieCryptoError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await append_log(
        "INFO",
        "session",
        f"Verificação de sessão: {result.status.value}",
    )
    await ws_manager.broadcast(
        "authentication_status",
        {
            "status": result.status.value,
            "account_name": result.account_name,
            "detail": result.detail,
        },
    )
    return {
        "status": result.status.value,
        "account_name": result.account_name,
        "detail": result.detail,
        "cookies": await steam_session.cookie_status(session),
    }


@router.post("/api/session/apply", response_model=MessageOut)
async def apply_session(session: AsyncSession = Depends(get_db)) -> MessageOut:
    try:
        await steam_session.apply_cookies(session)
    except CookieCryptoError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await append_log("INFO", "session", "Cookies reaplicados no navegador")
    await ws_manager.broadcast("browser_status", {})
    return MessageOut(message="Cookies reaplicados")
