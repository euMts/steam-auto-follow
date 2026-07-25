from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.manager import BrowserManager, BrowserNotRunningError, browser_manager
from app.config import (
    COMMUNITY_BOOTSTRAP_URL,
    COMMUNITY_COOKIE_DOMAINS,
    STORE_BOOTSTRAP_URL,
    STORE_COOKIE_DOMAINS,
    get_settings,
)
from app.models import AuthStatus, EncryptedCookie
from app.utils.crypto import CookieCryptoError, decrypt_value, encrypt_value
from app.utils.url_validation import mask_secret


@dataclass
class DomainCookieValues:
    steam_login_secure: str | None = None
    sessionid: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.steam_login_secure and self.sessionid)


@dataclass
class CookieValues:
    store: DomainCookieValues = field(default_factory=DomainCookieValues)
    community: DomainCookieValues = field(default_factory=DomainCookieValues)

    @property
    def configured(self) -> bool:
        return self.store.configured and self.community.configured


@dataclass
class AuthCheckResult:
    status: AuthStatus
    account_name: str | None = None
    detail: str | None = None


def _cookie_key(site: str, name: str) -> str:
    return f"{site}.{name}"


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
            values.store.steam_login_secure = self._read_cookie(
                rows, "store", "steamLoginSecure", legacy_names=("steamLoginSecure",)
            )
            values.store.sessionid = self._read_cookie(
                rows, "store", "sessionid", legacy_names=("sessionid",)
            )
            values.community.steam_login_secure = self._read_cookie(
                rows, "community", "steamLoginSecure"
            )
            values.community.sessionid = self._read_cookie(
                rows, "community", "sessionid"
            )
        except CookieCryptoError as exc:
            # Não apaga o banco — só indica falha de chave para o operador
            print(f"[cookies] Falha ao descriptografar ({len(rows)} registro(s)): {exc}")
            return CookieValues()
        return values

    def _read_cookie(
        self,
        rows: dict,
        site: str,
        name: str,
        *,
        legacy_names: tuple[str, ...] = (),
    ) -> str | None:
        key = _cookie_key(site, name)
        if key in rows:
            return decrypt_value(rows[key].value_encrypted)
        for legacy in legacy_names:
            if legacy in rows:
                return decrypt_value(rows[legacy].value_encrypted)
        return None

    async def cookie_status(self, session: AsyncSession) -> dict:
        values = await self.get_cookies(session)

        def site_status(domain: DomainCookieValues) -> dict:
            return {
                "steam_login_secure": (
                    "Configurado" if domain.steam_login_secure else "Não configurado"
                ),
                "sessionid": "Configurado" if domain.sessionid else "Não configurado",
                "steam_login_secure_masked": mask_secret(domain.steam_login_secure),
                "sessionid_masked": mask_secret(domain.sessionid),
                "configured": domain.configured,
            }

        store = site_status(values.store)
        community = site_status(values.community)
        return {
            "store": store,
            "community": community,
            "configured": values.configured,
            # Compat com UI antiga
            "steam_login_secure": (
                "Configurado" if values.configured else "Não configurado"
            ),
            "sessionid": "Configurado" if values.configured else "Não configurado",
            "steam_login_secure_masked": values.store.steam_login_secure
            and mask_secret(values.store.steam_login_secure),
            "sessionid_masked": values.store.sessionid and mask_secret(values.store.sessionid),
        }

    async def save_cookies(
        self,
        session: AsyncSession,
        *,
        store_steam_login_secure: str,
        store_sessionid: str,
        community_steam_login_secure: str,
        community_sessionid: str,
    ) -> None:
        await self._upsert_cookie(
            session, _cookie_key("store", "steamLoginSecure"), store_steam_login_secure
        )
        await self._upsert_cookie(
            session, _cookie_key("store", "sessionid"), store_sessionid
        )
        await self._upsert_cookie(
            session,
            _cookie_key("community", "steamLoginSecure"),
            community_steam_login_secure,
        )
        await self._upsert_cookie(
            session, _cookie_key("community", "sessionid"), community_sessionid
        )

        # Remove chaves legadas de par único, se existirem
        for legacy in ("steamLoginSecure", "sessionid"):
            result = await session.execute(
                select(EncryptedCookie).where(EncryptedCookie.name == legacy)
            )
            row = result.scalar_one_or_none()
            if row:
                await session.delete(row)

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
            raise CookieCryptoError(
                "Cookies da Store e da Community precisam estar configurados"
            )

        await self.browser.ensure_open()
        assert cookies.store.steam_login_secure and cookies.store.sessionid
        assert cookies.community.steam_login_secure and cookies.community.sessionid

        # Store
        await self.browser.goto(STORE_BOOTSTRAP_URL)
        await self.browser.context.add_cookies(
            self._build_cookie_payload(
                cookies.store.steam_login_secure,
                cookies.store.sessionid,
                STORE_COOKIE_DOMAINS,
            )
        )
        await self.browser.reload()

        # Community (valores distintos)
        await self.browser.goto(COMMUNITY_BOOTSTRAP_URL)
        await self.browser.context.add_cookies(
            self._build_cookie_payload(
                cookies.community.steam_login_secure,
                cookies.community.sessionid,
                COMMUNITY_COOKIE_DOMAINS,
            )
        )
        await self.browser.reload()

        self.browser.state.last_action = "Cookies aplicados (Store + Community separados)"

    def _build_cookie_payload(
        self,
        steam_login_secure: str,
        sessionid: str,
        domains: tuple[str, ...],
    ) -> list[dict]:
        payload: list[dict] = []
        for domain in domains:
            payload.append(
                {
                    "name": "steamLoginSecure",
                    "value": steam_login_secure,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                }
            )
            payload.append(
                {
                    "name": "sessionid",
                    "value": sessionid,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                }
            )
        return payload

    async def verify_session(
        self,
        session: AsyncSession,
        *,
        reapply: bool = True,
    ) -> AuthCheckResult:
        cookies = await self.get_cookies(session)
        if not cookies.configured:
            missing = []
            if not cookies.store.configured:
                missing.append("Store")
            if not cookies.community.configured:
                missing.append("Community")
            result = AuthCheckResult(
                status=AuthStatus.COOKIES_MISSING,
                detail=f"Cookies não configurados: {', '.join(missing)}",
            )
            self._set_auth(result)
            return result

        self._auth_status = AuthStatus.VERIFYING
        try:
            if reapply:
                await self.apply_cookies(session)
            elif not self.browser.is_open:
                await self.apply_cookies(session)

            # Verifica na Store (origem principal das tarefas)
            await self.browser.goto(STORE_BOOTSTRAP_URL)
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

        if "login" in page.url.lower() and "steampowered.com" in page.url.lower():
            return AuthCheckResult(
                status=AuthStatus.NOT_AUTHENTICATED,
                detail="Redirecionado para página de login",
            )

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
