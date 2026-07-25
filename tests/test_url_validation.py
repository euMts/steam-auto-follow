from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import TaskCreate
from app.utils.url_validation import InvalidSteamUrlError, detect_action_type, mask_secret, sanitize_log_message, validate_steam_url


class TestValidateSteamUrl:
    def test_accepts_curator_https(self):
        url = "https://store.steampowered.com/curator/43562394/"
        assert validate_steam_url(url) == url

    def test_accepts_publisher(self):
        url = "https://store.steampowered.com/publisher/asd/"
        assert validate_steam_url(url) == url

    def test_accepts_group(self):
        url = "https://steamcommunity.com/groups/63eReg"
        assert validate_steam_url(url) == url

    def test_accepts_community(self):
        url = "https://steamcommunity.com/groups/example"
        assert validate_steam_url(url) == url

    def test_rejects_http(self):
        with pytest.raises(InvalidSteamUrlError):
            validate_steam_url("http://store.steampowered.com/curator/1/")

    def test_rejects_file_scheme(self):
        with pytest.raises(InvalidSteamUrlError):
            validate_steam_url("file:///etc/passwd")

    def test_rejects_javascript(self):
        with pytest.raises(InvalidSteamUrlError):
            validate_steam_url("javascript:alert(1)")

    def test_rejects_data(self):
        with pytest.raises(InvalidSteamUrlError):
            validate_steam_url("data:text/html,hi")

    def test_rejects_localhost(self):
        with pytest.raises(InvalidSteamUrlError):
            validate_steam_url("https://localhost/admin")

    def test_rejects_arbitrary_domain(self):
        with pytest.raises(InvalidSteamUrlError):
            validate_steam_url("https://example.com/")

    def test_rejects_ip(self):
        with pytest.raises(InvalidSteamUrlError):
            validate_steam_url("https://192.168.0.1/")


class TestDetectActionType:
    def test_curator(self):
        assert detect_action_type("https://store.steampowered.com/curator/43562394/") == "follow_curator"

    def test_publisher(self):
        assert detect_action_type("https://store.steampowered.com/publisher/asd/") == "follow_publisher"

    def test_developer(self):
        assert detect_action_type("https://store.steampowered.com/developer/valve/") == "follow_publisher"

    def test_group(self):
        assert detect_action_type("https://steamcommunity.com/groups/63eReg") == "follow_group"

    def test_app(self):
        assert (
            detect_action_type("https://store.steampowered.com/app/3034600/Sandy_Planet__Season_1/")
            == "wishlist_and_follow_app"
        )

    def test_unknown(self):
        with pytest.raises(InvalidSteamUrlError):
            detect_action_type("https://store.steampowered.com/")


class TestTaskCreateValidation:
    def test_parses_multiple_urls(self):
        payload = TaskCreate(
            urls=(
                "https://store.steampowered.com/curator/1/\n"
                "https://store.steampowered.com/curator/2/"
            )
        )
        assert len(payload.parsed_urls()) == 2

    def test_auto_resolves_mixed_urls(self):
        payload = TaskCreate(
            urls=(
                "https://store.steampowered.com/publisher/asd/\n"
                "https://steamcommunity.com/groups/63eReg\n"
                "https://store.steampowered.com/app/3034600/Sandy_Planet__Season_1/\n"
                "https://store.steampowered.com/curator/23741321/"
            ),
            action_type="auto",
        )
        items = payload.resolved_items()
        assert items[0][1] == "follow_publisher"
        assert items[1][1] == "follow_group"
        assert items[2][1] == "wishlist_and_follow_app"
        assert items[3][1] == "follow_curator"

    def test_rejects_invalid_line(self):
        with pytest.raises(ValidationError):
            TaskCreate(urls="https://evil.example/\nhttps://store.steampowered.com/curator/1/")


class TestSanitization:
    def test_mask_secret(self):
        assert mask_secret("abcdefghij") == "abc...hij"
        assert mask_secret("ab") == "***"

    def test_sanitize_log_hides_cookies(self):
        msg = "steamLoginSecure=supersecrettoken sessionid=abc123xyz"
        clean = sanitize_log_message(msg)
        assert "supersecrettoken" not in clean
        assert "abc123xyz" not in clean
        assert "REDACTED" in clean
