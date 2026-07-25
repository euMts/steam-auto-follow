from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from app.config import get_settings


@dataclass
class BrowserState:
    is_open: bool = False
    current_url: str | None = None
    last_navigation: str | None = None
    last_action: str | None = None
    closed_manually: bool = False
    last_closed_at: datetime | None = None


class BrowserNotRunningError(RuntimeError):
    pass


class BrowserManager:
    """Gerencia uma única instância Chromium + contexto + página principal."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = None  # asyncio.Lock criado no start
        self.state = BrowserState()
        self._closing = False

    async def _ensure_lock(self) -> Any:
        import asyncio

        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def is_open(self) -> bool:
        return bool(
            self._browser
            and self._browser.is_connected()
            and self._page
            and not self._page.is_closed()
        )

    @property
    def page(self) -> Page:
        if not self.is_open or self._page is None:
            raise BrowserNotRunningError("Navegador não está aberto")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if not self.is_open or self._context is None:
            raise BrowserNotRunningError("Contexto do navegador não está disponível")
        return self._context

    async def start(self) -> None:
        lock = await self._ensure_lock()
        async with lock:
            if self.is_open:
                self.state.closed_manually = False
                return

            settings = get_settings()
            self._closing = False
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=settings.playwright_headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="pt-BR",
            )
            self._context.set_default_navigation_timeout(settings.navigation_timeout_ms)
            self._context.set_default_timeout(settings.element_timeout_ms)
            self._page = await self._context.new_page()
            self._page.on("close", self._on_page_close)
            self._page.on("framenavigated", self._on_frame_navigated)
            self._browser.on("disconnected", self._on_browser_disconnected)

            self.state.is_open = True
            self.state.closed_manually = False
            self.state.last_action = "Navegador iniciado"
            await self._sync_url()
            await self._notify_status()

    async def restart(self) -> None:
        await self.close()
        await self.start()

    async def close(self) -> None:
        lock = await self._ensure_lock()
        async with lock:
            await self._close_unlocked()

    async def _close_unlocked(self) -> None:
        self._closing = True
        try:
            if self._page and not self._page.is_closed():
                await self._page.close()
        except Exception:
            pass
        finally:
            self._page = None

        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        finally:
            self._context = None

        try:
            if self._browser and self._browser.is_connected():
                await self._browser.close()
        except Exception:
            pass
        finally:
            self._browser = None

        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        finally:
            self._playwright = None

        self.state.is_open = False
        self.state.current_url = None
        self.state.last_action = "Navegador fechado"
        self._closing = False
        await self._notify_status()

    def _on_page_close(self, _page: Page) -> None:
        if self._closing:
            return
        self.state.is_open = False
        self.state.closed_manually = True
        self.state.last_closed_at = datetime.now(timezone.utc)
        self.state.last_action = "Navegador fechado manualmente"
        self.state.current_url = None
        self._schedule_notify()

    def _on_browser_disconnected(self) -> None:
        if self._closing:
            return
        self.state.is_open = False
        self.state.closed_manually = True
        self.state.last_closed_at = datetime.now(timezone.utc)
        self.state.last_action = "Navegador desconectado"
        self.state.current_url = None
        self._page = None
        self._context = None
        self._browser = None
        self._schedule_notify()

    def _on_frame_navigated(self, frame) -> None:
        if self._closing or not self._page or frame != self._page.main_frame:
            return
        self.state.current_url = frame.url
        self.state.last_navigation = datetime.now(timezone.utc).isoformat()
        self.state.last_action = f"Navegou para {frame.url}"
        self._schedule_notify()

    def _schedule_notify(self) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._notify_status())
        except RuntimeError:
            pass

    async def _notify_status(self) -> None:
        try:
            from app.services.websocket_manager import ws_manager

            await ws_manager.broadcast("browser_status", self.get_status_dict())
        except Exception:
            pass

    async def ensure_open(self) -> Page:
        if not self.is_open:
            await self.start()
        return self.page

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        page = await self.ensure_open()
        lock = await self._ensure_lock()
        async with lock:
            await page.goto(url, wait_until=wait_until)
            self.state.last_navigation = datetime.now(timezone.utc).isoformat()
            self.state.last_action = f"Navegou para {url}"
            await self._sync_url()
            await self._notify_status()

    async def reload(self) -> None:
        page = self.page
        lock = await self._ensure_lock()
        async with lock:
            await page.reload(wait_until="domcontentloaded")
            self.state.last_navigation = datetime.now(timezone.utc).isoformat()
            self.state.last_action = "Página recarregada"
            await self._sync_url()
            await self._notify_status()

    async def _sync_url(self) -> None:
        if self._page and not self._page.is_closed():
            self.state.current_url = self._page.url
        else:
            self.state.current_url = None

    async def refresh_state(self) -> BrowserState:
        connected = bool(self._browser and self._browser.is_connected())
        page_ok = bool(self._page and not self._page.is_closed())
        self.state.is_open = connected and page_ok
        if self.state.is_open:
            await self._sync_url()
        return self.state

    async def screenshot(self, path: str) -> None:
        page = self.page
        await page.screenshot(path=path, full_page=False)

    def get_status_dict(self) -> dict[str, Any]:
        return {
            "is_open": self.is_open,
            "current_url": self.state.current_url,
            "last_navigation": self.state.last_navigation,
            "last_action": self.state.last_action,
            "closed_manually": self.state.closed_manually,
        }


# Instância compartilhada da aplicação
browser_manager = BrowserManager()
