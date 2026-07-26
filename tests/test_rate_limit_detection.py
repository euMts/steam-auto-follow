from __future__ import annotations

import pytest
from playwright.async_api import Page

from app.browser.actions import ActionError, ActionErrorCode, detect_blocking_conditions


class FakeLocator:
    def __init__(self, text: str = "") -> None:
        self._text = text

    async def inner_text(self, timeout: int = 2000) -> str:
        return self._text


class FakePage:
    def __init__(self, url: str, html: str, body_text: str = "") -> None:
        self.url = url
        self._html = html
        self._body = body_text or html

    async def content(self) -> str:
        return self._html

    def locator(self, _selector: str) -> FakeLocator:
        return FakeLocator(self._body)


@pytest.mark.asyncio
async def test_detects_portuguese_rate_limit_ops_page():
    page = FakePage(
        url="https://store.steampowered.com/app/1/",
        html="<html><body><h1>Ops!</h1><p>Ocorreu um erro ao processar a sua solicitação:</p>"
        "<p>Você realizou solicitações demais recentemente. Aguarde e tente realizar "
        "a sua solicitação novamente mais tarde.</p></body></html>",
    )
    with pytest.raises(ActionError) as exc:
        await detect_blocking_conditions(page)  # type: ignore[arg-type]
    assert exc.value.code == ActionErrorCode.RATE_LIMIT


@pytest.mark.asyncio
async def test_ops_already_group_member_is_not_rate_limit():
    page = FakePage(
        url="https://steamcommunity.com/groups/StarChupa",
        html=(
            '<html><body><div class="error_ctn"><h1>Ops!</h1>'
            "<p>Ocorreu um erro ao processar a sua solicitação:</p>"
            "<h3>Você já é um membro deste grupo.</h3></div></body></html>"
        ),
    )
    with pytest.raises(ActionError) as exc:
        await detect_blocking_conditions(page)  # type: ignore[arg-type]
    assert exc.value.code == ActionErrorCode.ALREADY_FOLLOWING


@pytest.mark.asyncio
async def test_detects_english_too_many_requests():
    page = FakePage(
        url="https://store.steampowered.com/",
        html="<html>too many requests — try again later</html>",
    )
    with pytest.raises(ActionError) as exc:
        await detect_blocking_conditions(page)  # type: ignore[arg-type]
    assert exc.value.code == ActionErrorCode.RATE_LIMIT
