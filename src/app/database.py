"""
Database connection and session management
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from .config import settings

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)

# Create async session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Base class for models
Base = declarative_base()


async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add dial_code column if missing (migration for existing DBs)
        await conn.execute(
            __import__('sqlalchemy').text(
                "ALTER TABLE applicants ADD COLUMN dial_code VARCHAR(10) DEFAULT '+244'"
            )
        ) if await _column_missing(conn, "applicants", "dial_code") else None


async def _column_missing(conn, table: str, column: str) -> bool:
    """Check if a column is missing from a table (SQLite)"""
    result = await conn.execute(
        __import__('sqlalchemy').text(f"PRAGMA table_info({table})")
    )
    columns = [row[1] for row in result.fetchall()]
    return column not in columns


async def get_session() -> AsyncSession:
    """Get database session"""
    async with async_session() as session:
        yield session
