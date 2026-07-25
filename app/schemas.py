from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.models import ActionType, AuthStatus, TaskStatus
from app.utils.url_validation import (
    detect_action_type,
    filter_steam_urls,
    looks_like_steam_url,
    validate_steam_url,
)


class DomainCookieInput(BaseModel):
    steam_login_secure: str = Field(..., min_length=1, alias="steamLoginSecure")
    sessionid: str = Field(..., min_length=1)

    model_config = {"populate_by_name": True}

    @field_validator("steam_login_secure", "sessionid")
    @classmethod
    def strip_values(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Valor não pode ser vazio")
        return cleaned


class CookieInput(BaseModel):
    store: DomainCookieInput
    community: DomainCookieInput


class DomainCookieStatus(BaseModel):
    steam_login_secure: Literal["Configurado", "Não configurado"]
    sessionid: Literal["Configurado", "Não configurado"]
    steam_login_secure_masked: str | None = None
    sessionid_masked: str | None = None
    configured: bool


class CookieStatus(BaseModel):
    store: DomainCookieStatus
    community: DomainCookieStatus
    configured: bool
    # Campos legados (resumo)
    steam_login_secure: Literal["Configurado", "Não configurado"] | None = None
    sessionid: Literal["Configurado", "Não configurado"] | None = None
    steam_login_secure_masked: str | None = None
    sessionid_masked: str | None = None


class SettingsUpdate(BaseModel):
    min_task_interval_seconds: float | None = Field(default=None, ge=5.0, le=300.0)
    navigation_timeout_ms: int | None = Field(default=None, ge=5000, le=180000)
    element_timeout_ms: int | None = Field(default=None, ge=2000, le=120000)
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    jitter_seconds: float | None = Field(default=None, ge=0.0, le=60.0)
    action_settle_ms: int | None = Field(default=None, ge=500, le=15000)
    click_delay_ms_min: int | None = Field(default=None, ge=100, le=10000)
    click_delay_ms_max: int | None = Field(default=None, ge=100, le=15000)
    max_actions_per_hour: int | None = Field(default=None, ge=1, le=200)
    cooldown_after_rate_limit_seconds: float | None = Field(default=None, ge=30.0, le=3600.0)
    auth_recheck_every_n_tasks: int | None = Field(default=None, ge=1, le=50)


class SettingsOut(BaseModel):
    min_task_interval_seconds: float
    navigation_timeout_ms: int
    element_timeout_ms: int
    max_attempts: int
    jitter_seconds: float
    action_settle_ms: int
    click_delay_ms_min: int
    click_delay_ms_max: int
    max_actions_per_hour: int
    cooldown_after_rate_limit_seconds: float
    auth_recheck_every_n_tasks: int
    rate_limit_cooldown_until: datetime | None = None
    adaptive_multiplier: float = 1.0
    actions_last_hour: int = 0
    playwright_headless: bool
    app_host: str
    app_port: int
    steam_base_url: str


class TaskCreate(BaseModel):
    urls: str = Field(..., min_length=1, description="Uma ou mais URLs, uma por linha")
    action_type: ActionType = ActionType.AUTO

    @field_validator("urls")
    @classmethod
    def validate_urls_block(cls, value: str) -> str:
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if not lines:
            raise ValueError("Informe ao menos uma URL")
        steam_lines = filter_steam_urls(lines)
        if not steam_lines:
            raise ValueError(
                "Nenhuma URL da Steam encontrada. "
                "Links sem 'steam' são ignorados automaticamente."
            )
        for line in steam_lines:
            validate_steam_url(line)
        return value

    def parsed_urls(self) -> list[str]:
        lines = [line.strip() for line in self.urls.splitlines() if line.strip()]
        return filter_steam_urls(lines)

    def skipped_urls(self) -> list[str]:
        lines = [line.strip() for line in self.urls.splitlines() if line.strip()]
        return [u for u in lines if not looks_like_steam_url(u)]

    def resolved_items(self) -> list[tuple[str, str]]:
        """Retorna pares (url, action_type) já resolvidos."""
        items: list[tuple[str, str]] = []
        for url in self.parsed_urls():
            if self.action_type == ActionType.AUTO:
                action = detect_action_type(url)
            else:
                action = self.action_type.value
            items.append((url, action))
        return items


class TaskOut(BaseModel):
    id: int
    url: str
    action_type: str
    status: str
    attempts: int
    max_attempts: int
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None
    result_message: str | None
    position: int
    updated_at: datetime | None
    current_step: str | None
    screenshot_path: str | None
    has_screenshot: bool = False

    model_config = {"from_attributes": True}


class QueueStats(BaseModel):
    pending: int
    running: int
    completed: int
    failed: int
    cancelled: int
    paused: bool
    manual_action_required: bool
    manual_action_message: str | None = None


class BrowserStatus(BaseModel):
    is_open: bool
    current_url: str | None = None
    last_navigation: str | None = None
    last_action: str | None = None
    closed_manually: bool = False


class AuthStatusOut(BaseModel):
    status: AuthStatus
    account_name: str | None = None
    checked_at: datetime | None = None
    cookies: CookieStatus


class LogOut(BaseModel):
    id: int
    level: str
    source: str
    message: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CurrentTaskOut(BaseModel):
    id: int | None = None
    url: str | None = None
    action_type: str | None = None
    current_step: str | None = None
    started_at: datetime | None = None
    attempts: int | None = None
    status_message: str | None = None


class DashboardStatus(BaseModel):
    browser: BrowserStatus
    authentication: AuthStatusOut
    queue: QueueStats
    current_task: CurrentTaskOut
    recent_logs: list[LogOut] = Field(default_factory=list)
    settings: SettingsOut
    last_action: str | None = None
    current_url: str | None = None


class WsEvent(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class MessageOut(BaseModel):
    message: str
    detail: str | None = None


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int
