from __future__ import annotations

from dataclasses import dataclass, field
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


UNFOLLOW_HINTS = (
    "deixar de seguir",
    "parar de seguir",
    "unfollow",
    "following",
    "seguindo",
    "leave group",
    "sair do grupo",
    "you're in",
    "você faz parte",
    "membro",
)

FOLLOW_HINTS = (
    "seguir",
    "follow",
    "join group",
    "entrar no grupo",
    "unir-se ao grupo",
    "participar",
)


@dataclass(frozen=True)
class FollowActionConfig:
    label: str
    entity_name: str
    selectors: tuple[str, ...]
    already_done_message: str
    success_message: str
    confirm_selectors: tuple[str, ...] = field(default_factory=tuple)


CURATOR_SELECTORS = (
    'role=button[name=/seguir/i]',
    "text=Seguir",
    "text=Follow",
    "#header_curator_details .follow_controls .follow_btn",
    "#header_curator_details .follow_btn",
    ".follow_controls .follow_btn",
    "div.follow_btn",
    'xpath=//*[@id="header_curator_details"]//div[contains(@class,"follow_btn")]',
    'xpath=//*[@id="header_curator_details"]/div[2]/div[1]',
    'xpath=/html/body/div[1]/div[6]/div[7]/div[3]/div[2]/div/div/div[2]/div[2]/div[1]',
)

PUBLISHER_SELECTORS = (
    'role=button[name=/seguir/i]',
    "text=Seguir",
    "text=Follow",
    ".follow_btn",
    ".follow_controls .follow_btn",
    ".queue_control_button.follow",
    "#wishlist_follow",
    "div.btn_green_steamui.btn_medium",
    'xpath=//*[contains(@class,"follow_btn")]',
    'xpath=//*[contains(@class,"follow_controls")]//*[contains(@class,"btn")]',
)

GROUP_SELECTORS = (
    'role=button[name=/entrar no grupo/i]',
    'role=button[name=/join group/i]',
    'role=link[name=/entrar no grupo/i]',
    'role=link[name=/join group/i]',
    "text=Entrar no grupo",
    "text=Unir-se ao grupo",
    "text=Join Group",
    "text=Seguir",
    "text=Follow",
    "#join_group_form .btn_green_white_innerfade",
    ".grouppage_join_area .btn_green_white_innerfade",
    ".grouppage_join_area a.btn_green_white_innerfade",
    "a.btn_green_white_innerfade",
    'xpath=//*[contains(@class,"grouppage_join_area")]//a[contains(@class,"btn")]',
    'xpath=//*[@id="join_group_form"]//a[contains(@class,"btn")]',
)

ACTION_CONFIGS: dict[str, FollowActionConfig] = {
    "follow_curator": FollowActionConfig(
        label="Seguir curador",
        entity_name="curador",
        selectors=CURATOR_SELECTORS,
        already_done_message="Curador já estava sendo seguido",
        success_message="Curador seguido com sucesso",
        confirm_selectors=(
            "text=Seguindo",
            "text=Following",
            "text=Deixar de seguir",
            "#header_curator_details .follow_btn.following",
            "#header_curator_details .follow_controls .following",
        ),
    ),
    "follow_publisher": FollowActionConfig(
        label="Seguir publisher",
        entity_name="publisher",
        selectors=PUBLISHER_SELECTORS,
        already_done_message="Publisher já estava sendo seguido",
        success_message="Publisher seguido com sucesso",
        confirm_selectors=(
            "text=Seguindo",
            "text=Following",
            "text=Deixar de seguir",
            "text=Unfollow",
            ".follow_btn.following",
            ".following",
        ),
    ),
    "follow_group": FollowActionConfig(
        label="Entrar no grupo",
        entity_name="grupo",
        selectors=GROUP_SELECTORS,
        already_done_message="Já é membro do grupo",
        success_message="Entrou no grupo com sucesso",
        confirm_selectors=(
            "text=You're In",
            "text=Leave Group",
            "text=Sair do grupo",
            "text=Você faz parte",
            "text=Seguindo",
            "text=Following",
            ".grouppage_join_area .btn_grey_black",
        ),
    ),
}


class FollowSteamEntityAction:
    """Ação genérica de seguir/entrar para curador, publisher ou grupo."""

    def __init__(self, browser: BrowserManager, config: FollowActionConfig) -> None:
        self.browser = browser
        self.config = config

    async def run(self, url: str, *, step_callback=None) -> ActionResult:
        async def step(name: str) -> None:
            if step_callback:
                await step_callback(name)

        settings = get_settings()
        await self.browser.ensure_open()

        await step("Abrindo URL")
        try:
            await self.browser.goto(url)
        except PlaywrightTimeoutError as exc:
            raise ActionError(
                ActionErrorCode.TIMEOUT,
                f"Timeout ao navegar para a URL do {self.config.entity_name}",
                retryable=True,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ActionError(
                ActionErrorCode.NAVIGATION_ERROR,
                f"Erro de navegação: {exc}",
                retryable=True,
            ) from exc

        page = self.browser.page
        await self._detect_blocking_conditions(page)

        await step("Procurando botão de seguir")
        button = await self._find_follow_button(page)

        await step("Verificando estado atual")
        state = await self._button_state(button)

        if state == "following":
            return ActionResult(
                success=True,
                message=self.config.already_done_message,
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
        return ActionResult(success=True, message=self.config.success_message)

    async def _detect_blocking_conditions(self, page: Page) -> None:
        url = page.url.lower()
        content = ""
        try:
            content = (await page.content()).lower()
        except Exception:
            pass

        if "login" in url and ("steampowered" in url or "steamcommunity" in url):
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

        for selector in self.config.selectors:
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

                if any(h in text for h in ("deixar", "parar", "unfollow", "leave group", "sair do grupo")):
                    return locator

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
            if await button.is_disabled():
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

        already_done_tokens = (
            "deixar de seguir",
            "parar de seguir",
            "unfollow",
            "seguindo",
            "following",
            "leave group",
            "sair do grupo",
            "you're in",
            "você faz parte",
        )
        if (
            "following" in classes
            or "followed" in classes
            or "unfollow" in classes
            or any(h in text for h in already_done_tokens)
        ):
            if text in ("seguir", "follow"):
                return "follow"
            if (
                any(h in text for h in FOLLOW_HINTS)
                and not any(h in text for h in ("deixar", "parar", "unfollow", "leave", "sair"))
            ):
                return "follow"
            return "following"

        aria = ""
        try:
            aria = (await button.get_attribute("aria-pressed") or "").lower()
        except Exception:
            pass
        if aria == "true":
            return "following"

        if any(h in text for h in FOLLOW_HINTS) or "follow_btn" in classes or "btn_green" in classes:
            return "follow"

        return "follow"

    async def _confirm_following(self, page: Page, button: Locator) -> bool:
        settings = get_settings()
        try:
            await page.wait_for_timeout(800)
        except Exception:
            pass

        try:
            if await self._button_state(button) == "following":
                return True
        except Exception:
            pass

        for selector in self.config.confirm_selectors:
            try:
                loc = page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible(
                    timeout=min(4000, settings.element_timeout_ms)
                ):
                    return True
            except Exception:
                continue
        return False


# Compatibilidade com imports existentes
class FollowCuratorAction(FollowSteamEntityAction):
    def __init__(self, browser: BrowserManager) -> None:
        super().__init__(browser, ACTION_CONFIGS["follow_curator"])


async def run_action(
    action_type: str,
    url: str,
    browser: BrowserManager,
    *,
    step_callback=None,
) -> ActionResult:
    config = ACTION_CONFIGS.get(action_type)
    if config is None:
        raise ActionError(
            ActionErrorCode.UNEXPECTED,
            f"Tipo de ação não suportado: {action_type}",
        )
    return await FollowSteamEntityAction(browser, config).run(url, step_callback=step_callback)
