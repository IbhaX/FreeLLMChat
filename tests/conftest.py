from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="freellm-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP}/test.db")
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # noqa
os.environ["ADMIN_TOKEN"] = "test-admin"
os.environ["GATEWAY_API_KEY"] = ""
os.environ["PROVIDERS_YAML"] = str(Path(__file__).parent.parent / "providers.yaml")

# Prevent env-key seeding from the real .env during tests
import app.providers.registry as _registry  # noqa: E402

_registry._env_value = lambda name: ""  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.models import ApiKey, Base, Provider  # noqa: E402


@pytest.fixture
async def engine():
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.fixture
async def provider(session) -> Provider:
    p = Provider(
        name="testprov", base_url="https://api.testprov.com/v1",
        rotation_strategy="least_used", limits={}, default_models=["m1", "m2"],
    )
    session.add(p)
    await session.commit()
    return p


async def make_key(
    session: AsyncSession,
    provider: Provider,
    raw: str = "sk-test-0001",
    **kwargs,
) -> ApiKey:
    from app.services.keys import add_key

    k = await add_key(session, provider, raw, **kwargs)
    await session.commit()
    assert k is not None
    return k
