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


# Curador e publisher usam o mesmo widget (#header_curator_details / CuratorFollowBtn_*).
CURATOR_STYLE_FOLLOW_SELECTORS = (
    '[id^="CuratorFollowBtn_"]',
    '#header_curator_details .follow_controls .follow_btn [role="button"]:not(.following)',
    "#header_curator_details .follow_controls .follow_btn .btn_green_steamui:not(.following)",
    '#header_curator_details div.follow_btn >> text=Seguir',
    '#header_curator_details div.follow_btn >> text=Follow',
    'xpath=//*[@id="header_curator_details"]//*[starts-with(@id,"CuratorFollowBtn_")]',
    'xpath=//*[@id="header_curator_details"]/div[2]/div[1]//span[contains(@id,"CuratorFollowBtn")]',
)

CURATOR_STYLE_FOLLOWING_SELECTORS = (
    '[id^="CuratorUnFollowBtn_"]:visible',
    "#header_curator_details .follow_btn .following:visible",
    '#header_curator_details .follow_btn >> text=Seguindo',
    '#header_curator_details .follow_btn >> text=Following',
)

GROUP_SELECTORS = (
    'role=button[name=/entrar no grupo/i]',
    'role=link[name=/entrar no grupo/i]',
    'role=button[name=/join group/i]',
    'role=link[name=/join group/i]',
    "text=Entrar no grupo",
    "text=Unir-se ao grupo",
    "text=Join Group",
    "#join_group_form .btn_green_white_innerfade",
    ".grouppage_join_area .btn_green_white_innerfade",
    ".grouppage_join_area a.btn_green_white_innerfade",
    'xpath=//*[contains(@class,"grouppage_join_area")]//a[contains(@class,"btn")]',
)

WISHLIST_SELECTORS = (
    "#add_to_wishlist_area a.add_to_wishlist",
    "#add_to_wishlist_area a",
    "#add_to_wishlist_area",
    'xpath=//*[@id="add_to_wishlist_area"]//a[contains(@class,"add_to_wishlist")]',
    'xpath=//*[@id="add_to_wishlist_area"]',
)

WISHLIST_DONE_SELECTORS = (
    "#add_to_wishlist_area_success:visible",
    "#add_to_wishlist_area_success",
    'text=Na Lista de Desejos',
    "text=On Wishlist",
    "#add_to_wishlist_area_success a",
)

APP_FOLLOW_SELECTORS = (
    "#queueBtnFollow button.queue_btn_inactive",
    '#queueBtnFollow button:not(.queue_btn_active)',
    '#queueBtnFollow >> text=Seguir',
    '#queueBtnFollow >> text=Follow',
    "#queueBtnFollow",
    'xpath=//*[@id="queueBtnFollow"]//button[contains(@class,"queue_btn_inactive")]',
    'xpath=//*[@id="queueBtnFollow"]',
)

APP_FOLLOWING_SELECTORS = (
    "#queueBtnFollow button.queue_btn_active:visible",
    '#queueBtnFollow >> text=Seguindo',
    '#queueBtnFollow >> text=Following',
)


async def _step(callback, name: str) -> None:
    if callback:
        await callback(name)


async def detect_blocking_conditions(page: Page) -> None:
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


async def _is_visible(locator: Locator, timeout: int = 1500) -> bool:
    try:
        if await locator.count() == 0:
            return False
        return await locator.is_visible(timeout=timeout)
    except Exception:
        return False


async def _first_visible(page: Page, selectors: tuple[str, ...], timeout: int = 2500) -> Locator | None:
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await _is_visible(loc, timeout=timeout):
                return loc
        except Exception:
            continue
    return None


async def _any_visible(page: Page, selectors: tuple[str, ...], timeout: int = 2500) -> bool:
    return await _first_visible(page, selectors, timeout=timeout) is not None


async def navigate(browser: BrowserManager, url: str, entity: str) -> Page:
    await browser.ensure_open()
    try:
        await browser.goto(url)
    except PlaywrightTimeoutError as exc:
        raise ActionError(
            ActionErrorCode.TIMEOUT,
            f"Timeout ao navegar para a URL do {entity}",
            retryable=True,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ActionError(
            ActionErrorCode.NAVIGATION_ERROR,
            f"Erro de navegação: {exc}",
            retryable=True,
        ) from exc
    page = browser.page
    await detect_blocking_conditions(page)
    return page


async def safe_click(locator: Locator) -> None:
    settings = get_settings()
    try:
        await locator.scroll_into_view_if_needed(timeout=settings.element_timeout_ms)
    except Exception:
        pass
    try:
        await locator.click(timeout=settings.element_timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise ActionError(
            ActionErrorCode.TIMEOUT,
            "Timeout ao clicar no elemento",
            retryable=True,
        ) from exc


class CuratorStyleFollowAction:
    """Seguir curador ou publisher (mesmo widget CuratorFollowBtn)."""

    def __init__(self, browser: BrowserManager, *, entity_name: str, success_message: str, already_message: str) -> None:
        self.browser = browser
        self.entity_name = entity_name
        self.success_message = success_message
        self.already_message = already_message

    async def run(self, url: str, *, step_callback=None) -> ActionResult:
        await _step(step_callback, "Abrindo URL")
        page = await navigate(self.browser, url, self.entity_name)

        await _step(step_callback, "Verificando estado atual")
        if await _any_visible(page, CURATOR_STYLE_FOLLOWING_SELECTORS):
            # Confirma que o botão "Seguir" está oculto
            follow = page.locator('[id^="CuratorFollowBtn_"]').first
            if await follow.count() > 0:
                style = (await follow.get_attribute("style") or "").lower()
                if "display: none" in style or not await _is_visible(follow, 800):
                    return ActionResult(
                        success=True,
                        message=self.already_message,
                        already_done=True,
                        code=ActionErrorCode.ALREADY_FOLLOWING,
                    )

        await _step(step_callback, "Procurando botão de seguir")
        button = await _first_visible(page, CURATOR_STYLE_FOLLOW_SELECTORS)
        if button is None:
            # Já seguindo: UnFollow visível e Follow oculto
            if await _any_visible(page, CURATOR_STYLE_FOLLOWING_SELECTORS, timeout=1500):
                return ActionResult(
                    success=True,
                    message=self.already_message,
                    already_done=True,
                    code=ActionErrorCode.ALREADY_FOLLOWING,
                )
            raise ActionError(
                ActionErrorCode.SELECTOR_NOT_FOUND,
                "Botão de seguir não encontrado na página",
                retryable=True,
            )

        text = ""
        try:
            text = (await button.inner_text()).strip().lower()
        except Exception:
            pass
        if "seguindo" in text or "following" in text:
            return ActionResult(
                success=True,
                message=self.already_message,
                already_done=True,
                code=ActionErrorCode.ALREADY_FOLLOWING,
            )

        await _step(step_callback, "Clicando em seguir")
        await safe_click(button)

        await _step(step_callback, "Confirmando resultado")
        await page.wait_for_timeout(900)
        if not await _any_visible(page, CURATOR_STYLE_FOLLOWING_SELECTORS, timeout=5000):
            # Recarrega e confere
            await self.browser.reload()
            if not await _any_visible(page, CURATOR_STYLE_FOLLOWING_SELECTORS, timeout=5000):
                raise ActionError(
                    ActionErrorCode.CONFIRMATION_MISSING,
                    "Clique executado, mas confirmação visual não encontrada",
                    retryable=True,
                )

        await _step(step_callback, "Finalizando")
        return ActionResult(success=True, message=self.success_message)


class FollowGroupAction:
    def __init__(self, browser: BrowserManager) -> None:
        self.browser = browser

    async def run(self, url: str, *, step_callback=None) -> ActionResult:
        await _step(step_callback, "Abrindo URL")
        page = await navigate(self.browser, url, "grupo")

        await _step(step_callback, "Procurando botão de entrar no grupo")
        already = (
            await _any_visible(page, ("text=Leave Group", "text=Sair do grupo", "text=You're In", "text=Você faz parte"))
        )
        if already:
            return ActionResult(
                success=True,
                message="Já é membro do grupo",
                already_done=True,
                code=ActionErrorCode.ALREADY_FOLLOWING,
            )

        button = await _first_visible(page, GROUP_SELECTORS)
        if button is None:
            raise ActionError(
                ActionErrorCode.SELECTOR_NOT_FOUND,
                "Botão de entrar no grupo não encontrado",
                retryable=True,
            )

        await _step(step_callback, "Clicando em entrar no grupo")
        await safe_click(button)

        await _step(step_callback, "Confirmando resultado")
        await page.wait_for_timeout(900)
        if not await _any_visible(
            page,
            ("text=Leave Group", "text=Sair do grupo", "text=You're In", "text=Você faz parte"),
            timeout=5000,
        ):
            raise ActionError(
                ActionErrorCode.CONFIRMATION_MISSING,
                "Clique executado, mas confirmação visual não encontrada",
                retryable=True,
            )

        await _step(step_callback, "Finalizando")
        return ActionResult(success=True, message="Entrou no grupo com sucesso")


class WishlistAndFollowAppAction:
    """Página de app: wishlist e depois follow (com reload entre os cliques)."""

    def __init__(self, browser: BrowserManager) -> None:
        self.browser = browser

    async def run(self, url: str, *, step_callback=None) -> ActionResult:
        await _step(step_callback, "Abrindo URL")
        page = await navigate(self.browser, url, "app")

        wishlist_done = False
        follow_done = False
        parts: list[str] = []

        await _step(step_callback, "Verificando lista de desejos")
        if await self._wishlist_already_done(page):
            wishlist_done = True
            parts.append("já estava na lista de desejos")
        else:
            await _step(step_callback, "Adicionando à lista de desejos")
            wishlist_btn = await _first_visible(page, WISHLIST_SELECTORS)
            if wishlist_btn is None:
                raise ActionError(
                    ActionErrorCode.SELECTOR_NOT_FOUND,
                    "Botão de lista de desejos não encontrado",
                    retryable=True,
                )
            await safe_click(wishlist_btn)
            await page.wait_for_timeout(1000)
            if not await self._wishlist_already_done(page):
                # Steam às vezes esconde a área e mostra success
                if not await _any_visible(page, WISHLIST_DONE_SELECTORS, timeout=4000):
                    raise ActionError(
                        ActionErrorCode.CONFIRMATION_MISSING,
                        "Falha ao confirmar adição à lista de desejos",
                        retryable=True,
                    )
            wishlist_done = True
            parts.append("adicionado à lista de desejos")

        await _step(step_callback, "Atualizando página")
        await self.browser.reload()
        page = self.browser.page
        await detect_blocking_conditions(page)

        await _step(step_callback, "Verificando follow do app")
        if await _any_visible(page, APP_FOLLOWING_SELECTORS):
            follow_done = True
            parts.append("já estava seguindo")
        else:
            await _step(step_callback, "Clicando em seguir")
            follow_btn = await _first_visible(page, APP_FOLLOW_SELECTORS)
            if follow_btn is None:
                raise ActionError(
                    ActionErrorCode.SELECTOR_NOT_FOUND,
                    "Botão de seguir do app não encontrado",
                    retryable=True,
                )

            # Preferir o botão inativo interno se o locator for o container
            try:
                inner = page.locator("#queueBtnFollow button.queue_btn_inactive").first
                if await _is_visible(inner, 1000):
                    follow_btn = inner
            except Exception:
                pass

            await safe_click(follow_btn)
            await page.wait_for_timeout(900)
            if not await _any_visible(page, APP_FOLLOWING_SELECTORS, timeout=5000):
                await self.browser.reload()
                if not await _any_visible(page, APP_FOLLOWING_SELECTORS, timeout=5000):
                    raise ActionError(
                        ActionErrorCode.CONFIRMATION_MISSING,
                        "Falha ao confirmar follow do app",
                        retryable=True,
                    )
            follow_done = True
            parts.append("app seguido")

        await _step(step_callback, "Finalizando")
        already = wishlist_done and follow_done and all(
            "já" in p for p in parts
        )
        return ActionResult(
            success=True,
            message="; ".join(parts).capitalize(),
            already_done=already,
            code=ActionErrorCode.ALREADY_FOLLOWING if already else None,
        )

    async def _wishlist_already_done(self, page: Page) -> bool:
        if await _any_visible(page, WISHLIST_DONE_SELECTORS, timeout=1200):
            return True
        # Área de adicionar oculta / sumiu
        add = page.locator("#add_to_wishlist_area").first
        if await add.count() > 0:
            style = (await add.get_attribute("style") or "").lower()
            if "display: none" in style:
                return True
            if not await _is_visible(add, 800):
                # Se success area existe
                if await page.locator("#add_to_wishlist_area_success").count() > 0:
                    return True
        return False


# Compat
class FollowCuratorAction(CuratorStyleFollowAction):
    def __init__(self, browser: BrowserManager) -> None:
        super().__init__(
            browser,
            entity_name="curador",
            success_message="Curador seguido com sucesso",
            already_message="Curador já estava sendo seguido",
        )


class FollowSteamEntityAction(CuratorStyleFollowAction):
    """Alias mantido para imports existentes."""

    def __init__(self, browser: BrowserManager, config=None) -> None:
        super().__init__(
            browser,
            entity_name="entidade",
            success_message="Seguido com sucesso",
            already_message="Já estava sendo seguido",
        )


async def run_action(
    action_type: str,
    url: str,
    browser: BrowserManager,
    *,
    step_callback=None,
) -> ActionResult:
    if action_type in ("follow_curator",):
        return await CuratorStyleFollowAction(
            browser,
            entity_name="curador",
            success_message="Curador seguido com sucesso",
            already_message="Curador já estava sendo seguido",
        ).run(url, step_callback=step_callback)

    if action_type in ("follow_publisher",):
        return await CuratorStyleFollowAction(
            browser,
            entity_name="publisher",
            success_message="Publisher seguido com sucesso",
            already_message="Publisher já estava sendo seguido",
        ).run(url, step_callback=step_callback)

    if action_type == "follow_group":
        return await FollowGroupAction(browser).run(url, step_callback=step_callback)

    if action_type == "wishlist_and_follow_app":
        return await WishlistAndFollowAppAction(browser).run(url, step_callback=step_callback)

    raise ActionError(
        ActionErrorCode.UNEXPECTED,
        f"Tipo de ação não suportado: {action_type}",
    )
