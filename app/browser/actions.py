from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from app.browser.manager import BrowserManager
from app.config import get_settings


class ActionErrorCode(str, Enum):
    COOKIES_MISSING = "cookies_missing"
    NOT_AUTHENTICATED = "not_authenticated"
    NAVIGATION_ERROR = "navigation_error"
    TIMEOUT = "timeout"
    SELECTOR_NOT_FOUND = "selector_not_found"
    BUTTON_INVISIBLE = "button_invisible"
    BUTTON_DISABLED = "button_disabled"
    ALREADY_FOLLOWING = "already_following"
    CONFIRMATION_MISSING = "confirmation_missing"
    CAPTCHA = "captcha"
    STEAM_GUARD = "steam_guard"
    LOGIN_PAGE = "login_page"
    RATE_LIMIT = "rate_limit"
    MANUAL_REQUIRED = "manual_required"
    UNEXPECTED = "unexpected"


class ActionError(Exception):
    def __init__(self, code: ActionErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class ActionResult:
    success: bool
    message: str
    already_done: bool = False
    code: ActionErrorCode | None = None


FOLLOW_BUTTON_SELECTORS = [
    # Semântico / texto
    'role=button[name=/seguir/i]',
    "text=Seguir",
    "text=Follow",
    # CSS estável
    "#header_curator_details .follow_controls .follow_btn",
    "#header_curator_details .follow_btn",
    ".follow_controls .follow_btn",
    "div.follow_btn",
    # XPath relativo
    'xpath=//*[@id="header_curator_details"]//div[contains(@class,"follow_btn")]',
    'xpath=//*[@id="header_curator_details"]/div[2]/div[1]',
    # XPath absoluto (último recurso)
    'xpath=/html/body/div[1]/div[6]/div[7]/div[3]/div[2]/div/div/div[2]/div[2]/div[1]',
]

UNFOLLOW_HINTS = (
    "deixar de seguir",
    "parar de seguir",
    "unfollow",
    "following",
    "seguindo",
)

FOLLOW_HINTS = ("seguir", "follow")


class FollowCuratorAction:
    def __init__(self, browser: BrowserManager) -> None:
        self.browser = browser

    async def run(self, url: str, *, step_callback=None) -> ActionResult:
        async def step(name: str) -> None:
            if step_callback:
                await step_callback(name)

        settings = get_settings()
        page = await self.browser.ensure_open()

        await step("Abrindo URL")
        try:
            await self.browser.goto(url)
        except PlaywrightTimeoutError as exc:
            raise ActionError(
                ActionErrorCode.TIMEOUT,
                "Timeout ao navegar para a URL do curador",
                retryable=True,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ActionError(
                ActionErrorCode.NAVIGATION_ERROR,
                f"Erro de navegação: {exc}",
                retryable=True,
            ) from exc

        await self._detect_blocking_conditions(page)

        await step("Procurando botão de seguir")
        button = await self._find_follow_button(page)

        await step("Verificando estado atual")
        state = await self._button_state(button)

        if state == "following":
            return ActionResult(
                success=True,
                message="Curador já estava sendo seguido",
                already_done=True,
                code=ActionErrorCode.ALREADY_FOLLOWING,
            )

        if state == "disabled":
            raise ActionError(
                ActionErrorCode.BUTTON_DISABLED,
                "Botão de seguir está desabilitado",
            )

        if state != "follow":
            raise ActionError(
                ActionErrorCode.SELECTOR_NOT_FOUND,
                "Elemento encontrado não representa a ação de seguir",
            )

        await step("Clicando em seguir")
        try:
            await button.click(timeout=settings.element_timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ActionError(
                ActionErrorCode.TIMEOUT,
                "Timeout ao clicar no botão de seguir",
                retryable=True,
            ) from exc

        await step("Confirmando resultado")
        confirmed = await self._confirm_following(page, button)
        if not confirmed:
            raise ActionError(
                ActionErrorCode.CONFIRMATION_MISSING,
                "Clique executado, mas confirmação visual não encontrada",
                retryable=True,
            )

        await step("Finalizando")
        return ActionResult(success=True, message="Curador seguido com sucesso")

    async def _detect_blocking_conditions(self, page: Page) -> None:
        url = page.url.lower()
        content = ""
        try:
            content = (await page.content()).lower()
        except Exception:
            pass

        if "login" in url and "steampowered" in url:
            raise ActionError(
                ActionErrorCode.LOGIN_PAGE,
                "Página de login detectada — ação manual necessária",
            )

        markers = [
            ("captcha", ActionErrorCode.CAPTCHA, "CAPTCHA detectado"),
            ("steam guard", ActionErrorCode.STEAM_GUARD, "Steam Guard detectado"),
            ("too many requests", ActionErrorCode.RATE_LIMIT, "Possível bloqueio temporário"),
            ("access denied", ActionErrorCode.RATE_LIMIT, "Acesso negado / bloqueio"),
        ]
        for token, code, message in markers:
            if token in content or token in url:
                raise ActionError(code, f"{message} — ação manual necessária")

    async def _find_follow_button(self, page: Page) -> Locator:
        settings = get_settings()
        last_error: Exception | None = None

        for selector in FOLLOW_BUTTON_SELECTORS:
            try:
                locator = page.locator(selector).first
                count = await locator.count()
                if count == 0:
                    continue
                visible = await locator.is_visible(timeout=min(3000, settings.element_timeout_ms))
                if not visible:
                    last_error = ActionError(
                        ActionErrorCode.BUTTON_INVISIBLE,
                        f"Botão encontrado mas invisível ({selector})",
                    )
                    continue

                text = ""
                try:
                    text = (await locator.inner_text()).strip().lower()
                except Exception:
                    text = ""

                # Evita clicar em "Deixar de seguir"
                if any(h in text for h in UNFOLLOW_HINTS) and not any(
                    h == text or text.startswith(h) for h in FOLLOW_HINTS if h == "seguir"
                ):
                    if "deixar" in text or "parar" in text or "unfollow" in text:
                        # Já seguindo — retorna o locator para inspeção de estado
                        return locator
                    continue

                return locator
            except ActionError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue

        if isinstance(last_error, ActionError):
            raise last_error
        raise ActionError(
            ActionErrorCode.SELECTOR_NOT_FOUND,
            "Botão de seguir não encontrado na página",
            retryable=True,
        )

    async def _button_state(self, button: Locator) -> str:
        try:
            if not await button.is_visible():
                return "invisible"
        except Exception:
            return "invisible"

        try:
            disabled = await button.is_disabled()
            if disabled:
                return "disabled"
        except Exception:
            pass

        classes = ""
        text = ""
        try:
            classes = (await button.get_attribute("class") or "").lower()
        except Exception:
            pass
        try:
            text = (await button.inner_text()).strip().lower()
        except Exception:
            pass

        if (
            "following" in classes
            or "followed" in classes
            or "unfollow" in classes
            or any(h in text for h in ("deixar de seguir", "parar de seguir", "unfollow", "seguindo"))
        ):
            # "Seguindo" / following state
            if "seguir" in text and "deixar" not in text and "parar" not in text:
                # texto curto "Seguir"
                pass
            else:
                return "following"

        aria = ""
        try:
            aria = (await button.get_attribute("aria-pressed") or "").lower()
        except Exception:
            pass
        if aria == "true":
            return "following"

        if any(h in text for h in FOLLOW_HINTS) or "follow_btn" in classes:
            return "follow"

        # Steam às vezes usa botão sem texto claro — assume follow se chegou aqui
        return "follow"

    async def _confirm_following(self, page: Page, button: Locator) -> bool:
        settings = get_settings()
        try:
            await page.wait_for_timeout(800)
        except Exception:
            pass

        # Reavalia o mesmo botão
        try:
            state = await self._button_state(button)
            if state == "following":
                return True
        except Exception:
            pass

        # Procura indicadores de "seguindo"
        confirm_selectors = [
            "text=Seguindo",
            "text=Following",
            "text=Deixar de seguir",
            "#header_curator_details .follow_btn.following",
            "#header_curator_details .follow_controls .following",
        ]
        for selector in confirm_selectors:
            try:
                loc = page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible(
                    timeout=min(4000, settings.element_timeout_ms)
                ):
                    return True
            except Exception:
                continue
        return False


async def run_action(
    action_type: str,
    url: str,
    browser: BrowserManager,
    *,
    step_callback=None,
) -> ActionResult:
    if action_type == "follow_curator":
        return await FollowCuratorAction(browser).run(url, step_callback=step_callback)
    raise ActionError(
        ActionErrorCode.UNEXPECTED,
        f"Tipo de ação não suportado: {action_type}",
    )
