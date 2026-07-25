from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
ENV_FILE = BASE_DIR / ".env"
COOKIE_KEY_FILE = DATA_DIR / ".cookie_key"
DEFAULT_DB_PATH = DATA_DIR / "app.db"

ALLOWED_STEAM_DOMAINS = (
    "store.steampowered.com",
    "steamcommunity.com",
    "help.steampowered.com",
    "checkout.steampowered.com",
)

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


def _default_database_url() -> str:
    # Caminho absoluto: não depende do cwd ao iniciar o servidor
    return f"sqlite+aiosqlite:///{DEFAULT_DB_PATH.resolve().as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    cookie_encryption_key: str = ""
    steam_base_url: str = "https://store.steampowered.com"
    playwright_headless: bool = False
    database_url: str = Field(default_factory=_default_database_url)
    min_task_interval_seconds: float = Field(default=20.0, ge=5.0, le=300.0)
    navigation_timeout_ms: int = Field(default=45000, ge=5000, le=180000)
    element_timeout_ms: int = Field(default=15000, ge=2000, le=120000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    jitter_seconds: float = Field(default=8.0, ge=0.0, le=60.0)
    action_settle_ms: int = Field(default=2500, ge=500, le=15000)
    click_delay_ms_min: int = Field(default=600, ge=100, le=10000)
    click_delay_ms_max: int = Field(default=1800, ge=100, le=15000)
    max_actions_per_hour: int = Field(default=25, ge=1, le=200)
    cooldown_after_rate_limit_seconds: float = Field(default=300.0, ge=30.0, le=3600.0)
    auth_recheck_every_n_tasks: int = Field(default=5, ge=1, le=50)
    log_buffer_size: int = 200


def _normalize_database_url(url: str) -> str:
    value = (url or "").strip()
    prefix = "sqlite+aiosqlite:///"
    if not value.startswith("sqlite+aiosqlite:"):
        return value

    rest = value.split(":///", 1)[-1] if ":///" in value else value
    rest = rest.lstrip("./")
    # Já absoluto (Unix /path ou Windows C:/path)
    if rest.startswith("/") or (len(rest) > 2 and rest[1] == ":"):
        return f"{prefix}{Path(rest).resolve().as_posix()}"
    return f"{prefix}{(BASE_DIR / rest).resolve().as_posix()}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    normalized = _normalize_database_url(settings.database_url)
    if normalized != settings.database_url:
        return settings.model_copy(update={"database_url": normalized})
    return settings


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_encryption_key() -> str:
    """Garante chave estável entre reinícios (env → arquivo → gera e persiste)."""
    ensure_data_dirs()
    get_settings.cache_clear()
    settings = get_settings()
    key = (settings.cookie_encryption_key or "").strip()
    if key:
        # Espelha no arquivo local para não perder se .env sumir
        if not COOKIE_KEY_FILE.exists():
            COOKIE_KEY_FILE.write_text(key, encoding="utf-8")
        return key

    if COOKIE_KEY_FILE.exists():
        file_key = COOKIE_KEY_FILE.read_text(encoding="utf-8").strip()
        if file_key:
            import os

            os.environ["COOKIE_ENCRYPTION_KEY"] = file_key
            get_settings.cache_clear()
            return file_key

    from app.utils.crypto import generate_encryption_key
    import os

    generated = generate_encryption_key()
    COOKIE_KEY_FILE.write_text(generated, encoding="utf-8")
    os.environ["COOKIE_ENCRYPTION_KEY"] = generated

    # Tenta gravar no .env se a chave estiver vazia/ausente
    _persist_key_to_env(generated)
    get_settings.cache_clear()
    return generated


def _persist_key_to_env(key: str) -> None:
    try:
        if ENV_FILE.exists():
            lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
            out: list[str] = []
            replaced = False
            for line in lines:
                if line.startswith("COOKIE_ENCRYPTION_KEY="):
                    current = line.split("=", 1)[1].strip()
                    if current:
                        return
                    out.append(f"COOKIE_ENCRYPTION_KEY={key}")
                    replaced = True
                else:
                    out.append(line)
            if not replaced:
                out.append(f"COOKIE_ENCRYPTION_KEY={key}")
            ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
        else:
            ENV_FILE.write_text(
                "APP_HOST=127.0.0.1\n"
                "APP_PORT=8000\n"
                f"COOKIE_ENCRYPTION_KEY={key}\n"
                "PLAYWRIGHT_HEADLESS=false\n",
                encoding="utf-8",
            )
    except OSError:
        pass
