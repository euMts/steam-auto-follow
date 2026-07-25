from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import TaskCreate
from app.utils.url_validation import InvalidSteamUrlError, mask_secret, sanitize_log_message, validate_steam_url


class TestValidateSteamUrl:
    def test_accepts_curator_https(self):
        url = "https://store.steampowered.com/curator/43562394/"
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


class TestTaskCreateValidation:
    def test_parses_multiple_urls(self):
        payload = TaskCreate(
            urls=(
                "https://store.steampowered.com/curator/1/\n"
                "https://store.steampowered.com/curator/2/"
            )
        )
        assert len(payload.parsed_urls()) == 2

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
