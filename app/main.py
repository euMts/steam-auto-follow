from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.browser.manager import browser_manager
from app.config import BASE_DIR, ensure_data_dirs, get_settings
from app.database import close_db, get_session_factory, init_db
from app.routes import browser, dashboard, settings, tasks
from app.services.queue_service import append_log, get_or_create_runtime, queue_service
from app.services.task_worker import task_worker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_data_dirs()
    settings = get_settings()
    if not settings.cookie_encryption_key.strip():
        # Gera chave temporária em memória se ausente — avisa nos logs
        from app.utils.crypto import generate_encryption_key
        import os

        generated = generate_encryption_key()
        os.environ["COOKIE_ENCRYPTION_KEY"] = generated
        get_settings.cache_clear()
        print(
            "[AVISO] COOKIE_ENCRYPTION_KEY não definida. "
            "Uma chave temporária foi gerada para esta execução. "
            "Defina no .env para persistir cookies entre reinícios."
        )

    await init_db()
    factory = get_session_factory()
    async with factory() as session:
        runtime = await get_or_create_runtime(session)
        # Sincroniza defaults do .env na primeira execução
        cfg = get_settings()
        if runtime.min_task_interval_seconds == 8.0:
            runtime.min_task_interval_seconds = cfg.min_task_interval_seconds
        if runtime.navigation_timeout_ms == 45000:
            runtime.navigation_timeout_ms = cfg.navigation_timeout_ms
        if runtime.element_timeout_ms == 15000:
            runtime.element_timeout_ms = cfg.element_timeout_ms
        if runtime.max_attempts == 3:
            runtime.max_attempts = cfg.max_attempts
        await session.commit()

        recovered = await queue_service.recover_interrupted(session)
        if recovered:
            await append_log(
                "INFO",
                "system",
                f"{recovered} tarefa(s) interrompida(s) reenfileirada(s) como pending",
            )

    task_worker.start()
    await append_log("INFO", "system", "Aplicação iniciada")
    # Acorda worker caso haja pendentes
    task_worker.wake()

    yield

    await append_log("INFO", "system", "Encerrando aplicação…")
    await task_worker.stop()
    await browser_manager.close()
    await close_db()


def create_app() -> FastAPI:
    application = FastAPI(
        title="Steam Task Runner",
        description="Automação local controlada de tarefas Steam via Playwright",
        version="1.0.0",
        lifespan=lifespan,
    )

    static_dir = BASE_DIR / "app" / "static"
    application.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    application.include_router(dashboard.router)
    application.include_router(settings.router)
    application.include_router(tasks.router)
    application.include_router(browser.router)

    return application


app = create_app()
