import aiosqlite
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/config/updatarr.db")
_FALLBACK_PATH = Path("updatarr.db")


def get_db_path():
    if DB_PATH.parent.exists():
        return DB_PATH
    return _FALLBACK_PATH


@dataclass
class HistoryEntry:
    id: int
    timestamp: str
    list_id: str
    list_name: str
    movie_title: str
    tmdb_id: int
    action: str
    details: str


@dataclass
class PendingDowngrade:
    id: int
    queued_at: str
    scheduled_for: str
    source_id: str
    source_name: str
    movie_title: str
    tmdb_id: int
    radarr_movie_id: int
    radarr_file_id: int
    current_profile: str
    target_profile: str
    plex_added_at: str
    status: str  # pending, excluded, cancelled, executed


async def init_db():
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                list_id TEXT NOT NULL,
                list_name TEXT NOT NULL,
                movie_title TEXT NOT NULL,
                tmdb_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_downgrades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queued_at TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                movie_title TEXT NOT NULL,
                tmdb_id INTEGER NOT NULL,
                radarr_movie_id INTEGER NOT NULL,
                radarr_file_id INTEGER NOT NULL,
                current_profile TEXT NOT NULL,
                target_profile TEXT NOT NULL,
                plex_added_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
            )
        """)
        await db.commit()
        # Migrations for existing databases
        try:
            await db.execute("ALTER TABLE pending_downgrades ADD COLUMN plex_added_at TEXT NOT NULL DEFAULT ''")
            await db.commit()
        except Exception:
            pass  # Column already exists


async def add_history_entry(list_id: str, list_name: str, movie_title: str,
                             tmdb_id: int, action: str, details: str = ""):
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "INSERT INTO history (timestamp, list_id, list_name, movie_title, tmdb_id, action, details) VALUES (?,?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), list_id, list_name, movie_title, tmdb_id, action, details)
        )
        await db.commit()


async def get_history(limit: int = 50) -> list[HistoryEntry]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [HistoryEntry(**dict(row)) for row in rows]


async def queue_downgrade(source_id: str, source_name: str, movie_title: str,
                           tmdb_id: int, radarr_movie_id: int, radarr_file_id: int,
                           current_profile: str, target_profile: str,
                           grace_days: int, plex_added_at: str = "") -> bool:
    """Queue a downgrade. Returns True if newly queued, False if already pending or excluded."""
    from datetime import timedelta
    now = datetime.utcnow()
    scheduled = now + timedelta(days=grace_days)
    async with aiosqlite.connect(get_db_path()) as db:
        # Skip if already pending or excluded
        async with db.execute(
            "SELECT id FROM pending_downgrades WHERE tmdb_id=? AND status IN ('pending', 'excluded')",
            (tmdb_id,)
        ) as cursor:
            if await cursor.fetchone():
                return False
        await db.execute(
            """INSERT INTO pending_downgrades
               (queued_at, scheduled_for, source_id, source_name, movie_title,
                tmdb_id, radarr_movie_id, radarr_file_id, current_profile, target_profile,
                plex_added_at, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending')""",
            (now.isoformat(), scheduled.isoformat(), source_id, source_name,
             movie_title, tmdb_id, radarr_movie_id, radarr_file_id,
             current_profile, target_profile, plex_added_at)
        )
        await db.commit()
    return True


def _make_pending(row: dict) -> PendingDowngrade:
    row.setdefault("plex_added_at", "")
    return PendingDowngrade(**row)


async def get_pending_downgrades(status: str = "pending") -> list[PendingDowngrade]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_downgrades WHERE status=? ORDER BY scheduled_for ASC",
            (status,)
        ) as cursor:
            return [_make_pending(dict(r)) for r in await cursor.fetchall()]


async def get_exclusions() -> list[PendingDowngrade]:
    """Return all excluded movies (status='excluded'), newest first."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_downgrades WHERE status='excluded' ORDER BY queued_at DESC"
        ) as cursor:
            return [_make_pending(dict(r)) for r in await cursor.fetchall()]


async def get_due_downgrades() -> list[PendingDowngrade]:
    """Return pending downgrades whose scheduled_for has passed."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_downgrades WHERE status='pending' AND scheduled_for <= ?",
            (now,)
        ) as cursor:
            return [_make_pending(dict(r)) for r in await cursor.fetchall()]


async def update_downgrade_status(downgrade_id: int, status: str):
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE pending_downgrades SET status=? WHERE id=?",
            (status, downgrade_id)
        )
        await db.commit()


async def exclude_downgrade(downgrade_id: int):
    """Mark a pending downgrade as excluded — skipped by all future syncs."""
    await update_downgrade_status(downgrade_id, "excluded")


async def restore_downgrade(downgrade_id: int):
    """Move an excluded movie back to pending."""
    await update_downgrade_status(downgrade_id, "pending")
