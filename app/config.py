from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"

ALLOWED_STEAM_DOMAINS = (
    "store.steampowered.com",
    "steamcommunity.com",
    "help.steampowered.com",
    "checkout.steampowered.com",
)

# Cookies distintos por site — não misturar valores entre domínios.
STORE_COOKIE_DOMAINS = (
    "store.steampowered.com",
    ".steampowered.com",
    "help.steampowered.com",
    "checkout.steampowered.com",
)

COMMUNITY_COOKIE_DOMAINS = (
    "steamcommunity.com",
    ".steamcommunity.com",
)

STORE_BOOTSTRAP_URL = "https://store.steampowered.com/"
COMMUNITY_BOOTSTRAP_URL = "https://steamcommunity.com/"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    cookie_encryption_key: str = ""
    steam_base_url: str = "https://store.steampowered.com"
    playwright_headless: bool = False
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    min_task_interval_seconds: float = Field(default=8.0, ge=1.0, le=300.0)
    navigation_timeout_ms: int = Field(default=45000, ge=5000, le=180000)
    element_timeout_ms: int = Field(default=15000, ge=2000, le=120000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    log_buffer_size: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
