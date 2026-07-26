from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from app.browser.manager import BrowserManager
from app.config import get_settings
from app.database import get_session_factory
from app.services.queue_service import get_or_create_runtime
from app.services.rate_limit_guard import rate_limit_guard


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
    ".grouppage_join_area a.btn_green_white_innerfade",
    ".grouppage_join_area a.btn_medium",
    'a.btn_green_white_innerfade:has-text("Entrar no grupo")',
    'a.btn_green_white_innerfade:has-text("Join Group")',
    'a.btn_green_white_innerfade:has-text("Unir-se ao grupo")',
    'role=link[name=/entrar no grupo/i]',
    'role=link[name=/join group/i]',
    "text=Entrar no grupo",
    "text=Unir-se ao grupo",
    "text=Join Group",
    "#join_group_form .btn_green_white_innerfade",
    'xpath=//*[@id="responsive_page_template_content"]//div[contains(@class,"grouppage_join_area")]//a[contains(@class,"btn")]',
    'xpath=//*[contains(@class,"grouppage_join_area")]//a[contains(@class,"btn")]',
)

GROUP_MEMBER_SELECTORS = (
    'form[name="leave_group_form"]',
    "form#leave_group_form",
    '.grouppage_join_area a:has-text("Sair do grupo")',
    '.grouppage_join_area a:has-text("Leave Group")',
    "text=Sair do grupo",
    "text=Leave Group",
    "text=You're In",
    "text=Você faz parte",
)

GROUP_JOIN_FORM_SELECTORS = (
    'form[name="join_group_form"]',
    "form#join_group_form",
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
    """Detecta CAPTCHA, rate limit, login e outras páginas que exigem ação manual."""
    url = page.url.lower()
    content = ""
    visible_text = ""
    try:
        content = (await page.content()).lower()
    except Exception:
        pass
    try:
        visible_text = (await page.locator("body").inner_text(timeout=2000)).lower()
    except Exception:
        pass
    haystack = f"{url}\n{content}\n{visible_text}"

    if "login" in url and ("steampowered" in url or "steamcommunity" in url):
        raise ActionError(
            ActionErrorCode.LOGIN_PAGE,
            "Página de login detectada — ação manual necessária",
        )

    rate_limit_markers = (
        "too many requests",
        "solicitações demais",
        "solicitacoes demais",
        "realizou solicitações demais",
        "realizou solicitacoes demais",
        "aguarde e tente realizar",
        "try again later",
        "rate limit",
        "você foi bloqueado temporariamente",
        "voce foi bloqueado temporariamente",
    )
    if any(token in haystack for token in rate_limit_markers):
        raise ActionError(
            ActionErrorCode.RATE_LIMIT,
            "Steam: solicitações demais recentemente — fila pausada. "
            "Aguarde no navegador e depois clique em Retomar fila",
        )

    # Página genérica "Ops!" da Steam com erro de solicitação
    if ("ops!" in haystack or "ops！" in haystack) and (
        "erro ao processar" in haystack
        or "error processing" in haystack
        or "sua solicitação" in haystack
        or "your request" in haystack
    ):
        raise ActionError(
            ActionErrorCode.RATE_LIMIT,
            "Steam exibiu página Ops!/erro de solicitação — ação manual necessária",
        )

    markers = [
        ("captcha", ActionErrorCode.CAPTCHA, "CAPTCHA detectado"),
        ("steam guard", ActionErrorCode.STEAM_GUARD, "Steam Guard detectado"),
        ("access denied", ActionErrorCode.RATE_LIMIT, "Acesso negado / bloqueio"),
        ("acesso negado", ActionErrorCode.RATE_LIMIT, "Acesso negado / bloqueio"),
    ]
    for token, code, message in markers:
        if token in haystack:
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
    await rate_limit_guard.human_pause(
        min_ms=500, max_ms=1400, reason="Pausa antes de navegar"
    )
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

    factory = get_session_factory()
    async with factory() as session:
        runtime = await get_or_create_runtime(session)
        await rate_limit_guard.settle_after_navigation(runtime)
    await detect_blocking_conditions(page)
    return page


async def safe_click(locator: Locator, page: Page | None = None) -> None:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        runtime = await get_or_create_runtime(session)
        await rate_limit_guard.pause_before_click(runtime)

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
    if page is not None:
        await page.wait_for_timeout(800)
        await detect_blocking_conditions(page)


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
        await safe_click(button, page)

        await _step(step_callback, "Confirmando resultado")
        await page.wait_for_timeout(900)
        if not await _any_visible(page, CURATOR_STYLE_FOLLOWING_SELECTORS, timeout=5000):
            # Recarrega e confere
            await self.browser.reload()
            await detect_blocking_conditions(page)
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

    async def _is_member(self, page: Page) -> bool:
        leave = page.locator('form[name="leave_group_form"], form#leave_group_form').first
        if await leave.count() > 0:
            return True
        return await _any_visible(page, GROUP_MEMBER_SELECTORS, timeout=1200)

    async def _join_form(self, page: Page) -> Locator | None:
        for selector in GROUP_JOIN_FORM_SELECTORS:
            loc = page.locator(selector).first
            if await loc.count() > 0:
                return loc
        return None

    async def _click_join(self, page: Page) -> None:
        """Entra no grupo via form submit (mais confiável que javascript: href)."""
        settings = get_settings()
        factory = get_session_factory()
        async with factory() as session:
            runtime = await get_or_create_runtime(session)
            await rate_limit_guard.pause_before_click(runtime)

        form = await self._join_form(page)
        button = await _first_visible(page, GROUP_SELECTORS, timeout=2000)
        if button is None:
            # Botão pode estar em .responsive_hidden (viewport estreito)
            button = page.locator(".grouppage_join_area a.btn_green_white_innerfade").first
            if await button.count() == 0:
                button = None

        try:
            if button is not None:
                try:
                    await button.scroll_into_view_if_needed(timeout=settings.element_timeout_ms)
                except Exception:
                    pass
                async with page.expect_navigation(
                    wait_until="domcontentloaded",
                    timeout=settings.navigation_timeout_ms,
                ):
                    await button.click(
                        timeout=settings.element_timeout_ms,
                        force=True,
                    )
                return

            if form is not None:
                async with page.expect_navigation(
                    wait_until="domcontentloaded",
                    timeout=settings.navigation_timeout_ms,
                ):
                    await form.evaluate("form => form.submit()")
                return
        except PlaywrightTimeoutError as exc:
            # Submit pode não disparar navigation em alguns layouts; segue para confirmação
            if form is not None:
                try:
                    await form.evaluate("form => form.submit()")
                    await page.wait_for_timeout(1200)
                    return
                except Exception:
                    pass
            raise ActionError(
                ActionErrorCode.TIMEOUT,
                "Timeout ao entrar no grupo",
                retryable=True,
            ) from exc

        raise ActionError(
            ActionErrorCode.SELECTOR_NOT_FOUND,
            "Botão/formulário de entrar no grupo não encontrado",
            retryable=True,
        )

    async def run(self, url: str, *, step_callback=None) -> ActionResult:
        await _step(step_callback, "Abrindo URL")
        page = await navigate(self.browser, url, "grupo")

        await _step(step_callback, "Verificando se já é membro")
        if await self._is_member(page):
            return ActionResult(
                success=True,
                message="Já é membro do grupo",
                already_done=True,
                code=ActionErrorCode.ALREADY_FOLLOWING,
            )

        join_form = await self._join_form(page)
        join_btn = await _first_visible(page, GROUP_SELECTORS, timeout=1500)
        has_hidden_btn = (
            await page.locator(".grouppage_join_area a.btn_green_white_innerfade").count()
        ) > 0
        if join_form is None and join_btn is None and not has_hidden_btn:
            raise ActionError(
                ActionErrorCode.SELECTOR_NOT_FOUND,
                "Botão de entrar no grupo não encontrado",
                retryable=True,
            )

        await _step(step_callback, "Clicando em entrar no grupo")
        await self._click_join(page)
        await detect_blocking_conditions(page)

        await _step(step_callback, "Confirmando resultado")
        await page.wait_for_timeout(800)
        if not await self._is_member(page):
            # Reload e confere (Steam às vezes atrasa o UI)
            await self.browser.reload()
            await detect_blocking_conditions(page)
            if not await self._is_member(page):
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
            await safe_click(wishlist_btn, page)
            await page.wait_for_timeout(1000)
            await detect_blocking_conditions(page)
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

        await _step(step_callback, "Pausa entre wishlist e follow")
        factory = get_session_factory()
        async with factory() as session:
            runtime = await get_or_create_runtime(session)
            await rate_limit_guard.pause_between_subactions(runtime)

        await _step(step_callback, "Atualizando página")
        await rate_limit_guard.human_pause(min_ms=800, max_ms=1600)
        await self.browser.reload()
        page = self.browser.page
        await detect_blocking_conditions(page)
        async with factory() as session:
            runtime = await get_or_create_runtime(session)
            await rate_limit_guard.settle_after_navigation(runtime)

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

            await safe_click(follow_btn, page)
            await page.wait_for_timeout(900)
            await detect_blocking_conditions(page)
            if not await _any_visible(page, APP_FOLLOWING_SELECTORS, timeout=5000):
                await self.browser.reload()
                await detect_blocking_conditions(page)
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
