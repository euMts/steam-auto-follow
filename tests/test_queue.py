from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models import TaskStatus
from app.services.queue_service import QueueService, get_or_create_runtime


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_claim_task(session: AsyncSession):
    qs = QueueService()
    tasks = await qs.create_tasks(
        session,
        urls=["https://store.steampowered.com/curator/1/"],
        action_type="follow_curator",
        max_attempts=3,
    )
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.PENDING.value

    claimed = await qs.claim_next(session)
    assert claimed is not None
    assert claimed.status == TaskStatus.RUNNING.value
    assert claimed.attempts == 1

    second = await qs.claim_next(session)
    assert second is None


@pytest.mark.asyncio
async def test_pause_blocks_claim(session: AsyncSession):
    qs = QueueService()
    await qs.create_tasks(
        session,
        urls=["https://store.steampowered.com/curator/2/"],
        action_type="follow_curator",
        max_attempts=3,
    )
    await qs.set_paused(session, True)
    assert await qs.claim_next(session) is None
    await qs.set_paused(session, False)
    assert await qs.claim_next(session) is not None


@pytest.mark.asyncio
async def test_fail_then_retry_transition(session: AsyncSession):
    qs = QueueService()
    created = await qs.create_tasks(
        session,
        urls=["https://store.steampowered.com/curator/3/"],
        action_type="follow_curator",
        max_attempts=2,
    )
    task = await qs.claim_next(session)
    assert task is not None

    await qs.fail_task(session, task.id, "timeout", retry=True)
    refreshed = await qs.get_task(session, task.id)
    assert refreshed is not None
    assert refreshed.status == TaskStatus.PENDING.value

    task2 = await qs.claim_next(session)
    assert task2 is not None
    await qs.fail_task(session, task2.id, "timeout", retry=True)
    refreshed2 = await qs.get_task(session, created[0].id)
    assert refreshed2 is not None
    assert refreshed2.status == TaskStatus.FAILED.value
    assert refreshed2.attempts == 2


@pytest.mark.asyncio
async def test_complete_and_clear(session: AsyncSession):
    qs = QueueService()
    await qs.create_tasks(
        session,
        urls=["https://store.steampowered.com/curator/4/"],
        action_type="follow_curator",
        max_attempts=3,
    )
    task = await qs.claim_next(session)
    await qs.complete_task(session, task.id, "ok")
    stats = await qs.stats(session)
    assert stats["completed"] == 1
    removed = await qs.clear_completed(session)
    assert removed == 1


@pytest.mark.asyncio
async def test_recover_interrupted_running(session: AsyncSession):
    qs = QueueService()
    await qs.create_tasks(
        session,
        urls=["https://store.steampowered.com/curator/5/"],
        action_type="follow_curator",
        max_attempts=3,
    )
    task = await qs.claim_next(session)
    assert task.status == TaskStatus.RUNNING.value
    recovered = await qs.recover_interrupted(session)
    assert recovered == 1
    refreshed = await qs.get_task(session, task.id)
    assert refreshed.status == TaskStatus.PENDING.value


@pytest.mark.asyncio
async def test_runtime_defaults(session: AsyncSession):
    runtime = await get_or_create_runtime(session)
    assert runtime.queue_paused is False
    again = await get_or_create_runtime(session)
    assert again.id == runtime.id
