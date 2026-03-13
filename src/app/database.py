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

        # Migrate existing face videos from face_photo_path dirs to videos table
        if not await _table_missing(conn, "videos"):
            await _migrate_face_videos_to_table(conn)


async def _table_missing(conn, table: str) -> bool:
    """Check if a table exists (SQLite)"""
    result = await conn.execute(text(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
    ))
    return result.fetchone() is None


async def _migrate_face_videos_to_table(conn):
    """One-time migration: move face_photo_path directory entries to videos table"""
    from pathlib import Path
    result = await conn.execute(text(
        "SELECT id, face_photo_path FROM applicants WHERE face_photo_path IS NOT NULL"
    ))
    rows = result.fetchall()
    for row in rows:
        applicant_id, face_path = row[0], row[1]
        if not face_path:
            continue
        p = Path(face_path)
        if p.is_dir():
            for f in sorted(p.iterdir()):
                if f.suffix.lower() in (".mp4", ".webm", ".ogg", ".mkv"):
                    # Check if already migrated
                    existing = await conn.execute(text(
                        "SELECT id FROM videos WHERE applicant_id = :aid AND file_path = :fp"
                    ), {"aid": applicant_id, "fp": str(f)})
                    if not existing.fetchone():
                        await conn.execute(text(
                            "INSERT INTO videos (applicant_id, file_path, filename, file_type, size_bytes) "
                            "VALUES (:aid, :fp, :fn, 'face_video', :sz)"
                        ), {"aid": applicant_id, "fp": str(f), "fn": f.name, "sz": f.stat().st_size})


async def _column_missing(conn, table: str, column: str) -> bool:
    """Check if a column is missing from a table (SQLite)"""
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    columns = [row[1] for row in result.fetchall()]
    return column not in columns


async def get_session() -> AsyncSession:
    """Get database session"""
    async with async_session() as session:
        yield session
