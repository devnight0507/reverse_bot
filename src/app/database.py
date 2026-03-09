"""
Database connection and session management
"""
from sqlalchemy import text
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
        # Add new columns if missing (migration for existing DBs)
        migrations = [
            ("dial_code", "VARCHAR(10) DEFAULT '+244'"),
            ("face_photo_path", "VARCHAR(500)"),
            ("passport_front_path", "VARCHAR(500)"),
            ("passport_page_path", "VARCHAR(500)"),
        ]
        for col_name, col_type in migrations:
            if await _column_missing(conn, "applicants", col_name):
                await conn.execute(text(
                    f"ALTER TABLE applicants ADD COLUMN {col_name} {col_type}"
                ))


async def _column_missing(conn, table: str, column: str) -> bool:
    """Check if a column is missing from a table (SQLite)"""
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    columns = [row[1] for row in result.fetchall()]
    return column not in columns


async def get_session() -> AsyncSession:
    """Get database session"""
    async with async_session() as session:
        yield session
