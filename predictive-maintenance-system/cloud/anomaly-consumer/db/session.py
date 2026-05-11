"""PostgreSQL 세션 팩토리 (SQLAlchemy 2.x async)"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=3,       # replica 2개 × 3 = 6 커넥션
    max_overflow=2,    # 버스트 시 replica당 +2 = 최대 10
    pool_timeout=30,
    pool_recycle=1800,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """테이블 자동 생성 (개발/CI용 — 프로덕션은 Alembic 마이그레이션 권장)"""
    from .models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
