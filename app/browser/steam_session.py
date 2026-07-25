from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.manager import BrowserManager, BrowserNotRunningError, browser_manager
from app.config import STEAM_COOKIE_BOOTSTRAP_URLS, STEAM_COOKIE_DOMAINS, get_settings
from app.models import AuthStatus, EncryptedCookie
from app.utils.crypto import CookieCryptoError, decrypt_value, encrypt_value
from app.utils.url_validation import mask_secret


COOKIE_NAMES = ("steamLoginSecure", "sessionid")


@dataclass
class CookieValues:
    steam_login_secure: str | None = None
    sessionid: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.steam_login_secure and self.sessionid)


@dataclass
class AuthCheckResult:
    status: AuthStatus
    account_name: str | None = None
    detail: str | None = None


class SteamSessionService:
    def __init__(self, browser: BrowserManager) -> None:
        self.browser = browser
        self._auth_status = AuthStatus.NOT_VERIFIED
        self._account_name: str | None = None
        self._checked_at: datetime | None = None

    @property
    def auth_status(self) -> AuthStatus:
        return self._auth_status

    @property
    def account_name(self) -> str | None:
        return self._account_name

    @property
    def checked_at(self) -> datetime | None:
        return self._checked_at

    async def get_cookies(self, session: AsyncSession) -> CookieValues:
        result = await session.execute(select(EncryptedCookie))
        rows = {row.name: row for row in result.scalars().all()}
        values = CookieValues()
        try:
            if "steamLoginSecure" in rows:
                values.steam_login_secure = decrypt_value(rows["steamLoginSecure"].value_encrypted)
            if "sessionid" in rows:
                values.sessionid = decrypt_value(rows["sessionid"].value_encrypted)
        except CookieCryptoError:
            return CookieValues()
        return values

    async def cookie_status(self, session: AsyncSession) -> dict:
        values = await self.get_cookies(session)
        return {
            "steam_login_secure": "Configurado" if values.steam_login_secure else "Não configurado",
            "sessionid": "Configurado" if values.sessionid else "Não configurado",
            "steam_login_secure_masked": mask_secret(values.steam_login_secure),
            "sessionid_masked": mask_secret(values.sessionid),
            "configured": values.configured,
        }

    async def save_cookies(
        self,
        session: AsyncSession,
        steam_login_secure: str,
        sessionid: str,
    ) -> None:
        await self._upsert_cookie(session, "steamLoginSecure", steam_login_secure)
        await self._upsert_cookie(session, "sessionid", sessionid)
        await session.commit()
        self._auth_status = AuthStatus.NOT_VERIFIED
        self._account_name = None
        self._checked_at = None

    async def clear_cookies(self, session: AsyncSession) -> None:
        result = await session.execute(select(EncryptedCookie))
        for row in result.scalars().all():
            await session.delete(row)
        await session.commit()
        self._auth_status = AuthStatus.COOKIES_MISSING
        self._account_name = None
        self._checked_at = datetime.now(timezone.utc)

    async def _upsert_cookie(self, session: AsyncSession, name: str, value: str) -> None:
        result = await session.execute(
            select(EncryptedCookie).where(EncryptedCookie.name == name)
        )
        row = result.scalar_one_or_none()
        encrypted = encrypt_value(value)
        if row:
            row.value_encrypted = encrypted
        else:
            session.add(EncryptedCookie(name=name, value_encrypted=encrypted))

    async def apply_cookies(self, session: AsyncSession) -> None:
        cookies = await self.get_cookies(session)
        if not cookies.configured:
            self._auth_status = AuthStatus.COOKIES_MISSING
            raise CookieCryptoError("Cookies da Steam não estão configurados")

        await self.browser.ensure_open()

        # Abre a Store primeiro (origem válida) antes de injetar cookies.
        await self.browser.goto(STEAM_COOKIE_BOOTSTRAP_URLS[0])

        payload = []
        assert cookies.steam_login_secure and cookies.sessionid
        for domain in STEAM_COOKIE_DOMAINS:
            payload.append(
                {
                    "name": "steamLoginSecure",
                    "value": cookies.steam_login_secure,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                }
            )
            payload.append(
                {
                    "name": "sessionid",
                    "value": cookies.sessionid,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                }
            )

        await self.browser.context.add_cookies(payload)

        # Garante sessão nos dois sites: Store e Community (mesmos nomes de cookie).
        for url in STEAM_COOKIE_BOOTSTRAP_URLS:
            await self.browser.goto(url)
            await self.browser.reload()

        self.browser.state.last_action = "Cookies aplicados (Store + Community)"

    async def verify_session(
        self,
        session: AsyncSession,
        *,
        reapply: bool = True,
    ) -> AuthCheckResult:
        cookies = await self.get_cookies(session)
        if not cookies.configured:
            result = AuthCheckResult(
                status=AuthStatus.COOKIES_MISSING,
                detail="Cookies não configurados",
            )
            self._set_auth(result)
            return result

        self._auth_status = AuthStatus.VERIFYING
        try:
            if reapply:
                await self.apply_cookies(session)
            elif not self.browser.is_open:
                await self.apply_cookies(session)
            page = self.browser.page
            result = await self._inspect_auth(page)
            self._set_auth(result)
            return result
        except BrowserNotRunningError as exc:
            result = AuthCheckResult(status=AuthStatus.ERROR, detail=str(exc))
            self._set_auth(result)
            return result
        except PlaywrightTimeoutError:
            result = AuthCheckResult(
                status=AuthStatus.ERROR,
                detail="Timeout ao verificar autenticação",
            )
            self._set_auth(result)
            return result
        except Exception as exc:  # noqa: BLE001
            result = AuthCheckResult(status=AuthStatus.ERROR, detail=str(exc))
            self._set_auth(result)
            return result

    def _set_auth(self, result: AuthCheckResult) -> None:
        self._auth_status = result.status
        self._account_name = result.account_name
        self._checked_at = datetime.now(timezone.utc)

    async def _inspect_auth(self, page: Page) -> AuthCheckResult:
        settings = get_settings()
        timeout = min(settings.element_timeout_ms, 12000)

        # Página de login explícita
        if "login" in page.url.lower() and "steampowered.com" in page.url.lower():
            return AuthCheckResult(
                status=AuthStatus.NOT_AUTHENTICATED,
                detail="Redirecionado para página de login",
            )

        # Indicadores positivos de sessão autenticada
        account_selectors = [
            "#account_pulldown",
            ".playerAvatar",
            "#account_dropdown",
            "a.user_avatar",
            "#header_wallet_balance",
        ]
        for selector in account_selectors:
            try:
                loc = page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible(timeout=1500):
                    name = await self._extract_account_name(page)
                    return AuthCheckResult(
                        status=AuthStatus.AUTHENTICATED,
                        account_name=name,
                        detail="Sessão autenticada",
                    )
            except Exception:
                continue

        # Logout link
        try:
            logout = page.get_by_role("link", name="Sair")
            if await logout.count() > 0:
                name = await self._extract_account_name(page)
                return AuthCheckResult(
                    status=AuthStatus.AUTHENTICATED,
                    account_name=name,
                    detail="Link de logout encontrado",
                )
        except Exception:
            pass

        # Botão/link de login visível sugere não autenticado
        login_selectors = [
            'a[href*="login"]',
            "#global_action_menu a.global_action_link",
            "text=iniciar sessão",
            "text=sign in",
            "text=login",
        ]
        for selector in login_selectors:
            try:
                loc = page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible(timeout=1000):
                    text = (await loc.inner_text()).strip().lower()
                    if any(token in text for token in ("iniciar", "sign in", "login", "entrar")):
                        return AuthCheckResult(
                            status=AuthStatus.NOT_AUTHENTICATED,
                            detail="Botão de login visível",
                        )
            except Exception:
                continue

        # Fallback: cookies presentes no contexto
        try:
            ctx_cookies = await page.context.cookies()
            names = {c["name"] for c in ctx_cookies if "steam" in c.get("domain", "")}
            if "steamLoginSecure" in names and "sessionid" in names:
                # Cookies presentes mas UI ambígua
                return AuthCheckResult(
                    status=AuthStatus.NOT_AUTHENTICATED,
                    detail="Cookies presentes, mas interface não confirma login",
                )
        except Exception:
            pass

        return AuthCheckResult(
            status=AuthStatus.ERROR,
            detail=f"Não foi possível determinar o estado da sessão (timeout={timeout}ms)",
        )

    async def _extract_account_name(self, page: Page) -> str | None:
        candidates = [
            "#account_pulldown",
            ".playerAvatar ~ a",
            "#account_dropdown .persona_name",
            ".persona_name_text_content",
        ]
        for selector in candidates:
            try:
                loc = page.locator(selector).first
                if await loc.count() == 0:
                    continue
                text = (await loc.inner_text()).strip()
                if text and len(text) < 64:
                    return text.split("\n")[0].strip()
            except Exception:
                continue
                return None


steam_session = SteamSessionService(browser_manager)
