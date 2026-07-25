from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActionType(str, enum.Enum):
    FOLLOW_CURATOR = "follow_curator"


class AuthStatus(str, enum.Enum):
    NOT_VERIFIED = "not_verified"
    VERIFYING = "verifying"
    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    COOKIES_MISSING = "cookies_missing"
    ERROR = "error"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TaskStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )
    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class EncryptedCookie(Base):
    __tablename__ = "encrypted_cookies"
    __table_args__ = (UniqueConstraint("name", name="uq_cookie_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class LogEntry(Base):
    __tablename__ = "log_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RuntimeState(Base):
    """Persisted runtime flags (queue pause, etc.)."""

    __tablename__ = "runtime_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    queue_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manual_action_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manual_action_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    min_task_interval_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=8.0)
    navigation_timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=45000)
    element_timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=15000)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    auth_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AuthStatus.NOT_VERIFIED.value,
    )
    auth_account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    auth_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_action: Mapped[str | None] = mapped_column(String(256), nullable=True)
    current_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
