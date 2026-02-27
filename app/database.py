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
class TaskSchedule:
    task_id: str
    task_name: str
    interval_minutes: int
    last_run: str | None
    enabled: bool


@dataclass
class RSSCacheEntry:
    url: str
    etag: str | None
    last_modified: str | None
    last_fetched: str


@dataclass
class PendingRetirement:
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
    status: str          # pending, excluded, cancelled, executed
    stage_days: int      # older_than_days of the stage that queued this (stage identity key)
    action: str          # redownload | reencode | archive | delete
    archived_path: str   # populated after archive executes, used by delete stage


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
                status TEXT NOT NULL DEFAULT 'pending',
                stage_days INTEGER NOT NULL DEFAULT 0,
                action TEXT NOT NULL DEFAULT 'redownload',
                archived_path TEXT NOT NULL DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rss_cache (
                url TEXT PRIMARY KEY,
                etag TEXT,
                last_modified TEXT,
                last_fetched TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS task_schedule (
                task_id TEXT PRIMARY KEY,
                task_name TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL,
                last_run TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            )
        """)
        # Seed default tasks (INSERT OR IGNORE so existing custom intervals are preserved)
        for task_id, task_name, interval in [
            ("plex_watchlist", "Plex Watchlist",   15),
            ("lists",          "Import Lists",     1440),
            ("retirement",     "Retirement Queue", 1440),
        ]:
            await db.execute(
                "INSERT OR IGNORE INTO task_schedule (task_id, task_name, interval_minutes) VALUES (?,?,?)",
                (task_id, task_name, interval)
            )
        await db.commit()
        # Migrations for existing databases
        for migration in [
            "ALTER TABLE pending_downgrades ADD COLUMN plex_added_at TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE pending_downgrades ADD COLUMN stage_days INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE pending_downgrades ADD COLUMN action TEXT NOT NULL DEFAULT 'redownload'",
            "ALTER TABLE pending_downgrades ADD COLUMN archived_path TEXT NOT NULL DEFAULT ''",
            # Rename legacy action value 'tdarr' → 'reencode'
            "UPDATE pending_downgrades SET action='reencode' WHERE action='tdarr'",
        ]:
            try:
                await db.execute(migration)
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
                           grace_days: int, plex_added_at: str = "",
                           stage_days: int = 0, action: str = "redownload") -> bool:
    """
    Queue a retirement stage entry.

    Returns True if newly queued, False if already handled.
    - Globally excluded movies (any excluded entry) are always skipped.
    - A specific (tmdb_id, stage_days) pair is skipped once pending or executed.
    """
    from datetime import timedelta
    now = datetime.utcnow()
    scheduled = now + timedelta(days=grace_days)
    async with aiosqlite.connect(get_db_path()) as db:
        # Skip if the movie is globally excluded
        async with db.execute(
            "SELECT id FROM pending_downgrades WHERE tmdb_id=? AND status='excluded'",
            (tmdb_id,)
        ) as cursor:
            if await cursor.fetchone():
                return False
        # Skip if this specific stage is already pending or executed
        async with db.execute(
            "SELECT id FROM pending_downgrades WHERE tmdb_id=? AND stage_days=? AND status IN ('pending', 'executed')",
            (tmdb_id, stage_days)
        ) as cursor:
            if await cursor.fetchone():
                return False
        await db.execute(
            """INSERT INTO pending_downgrades
               (queued_at, scheduled_for, source_id, source_name, movie_title,
                tmdb_id, radarr_movie_id, radarr_file_id, current_profile, target_profile,
                plex_added_at, status, stage_days, action)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
            (now.isoformat(), scheduled.isoformat(), source_id, source_name,
             movie_title, tmdb_id, radarr_movie_id, radarr_file_id,
             current_profile, target_profile, plex_added_at, stage_days, action)
        )
        await db.commit()
    return True


async def set_archived_path(entry_id: int, archived_path: str) -> None:
    """Store where a movie was archived — used later by the delete stage."""
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE pending_downgrades SET archived_path=? WHERE id=?",
            (archived_path, entry_id)
        )
        await db.commit()


async def get_executed_archive_path(tmdb_id: int) -> str | None:
    """Return the archived_path of the most recent executed archive entry for a movie."""
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute(
            """SELECT archived_path FROM pending_downgrades
               WHERE tmdb_id=? AND action='archive' AND status='executed'
               ORDER BY id DESC LIMIT 1""",
            (tmdb_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
            return None


async def get_all_archived_tmdb_ids() -> set[int]:
    """Return the set of tmdb_ids that have an executed archive entry."""
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute(
            "SELECT DISTINCT tmdb_id FROM pending_downgrades WHERE action='archive' AND status='executed'"
        ) as cursor:
            rows = await cursor.fetchall()
            return {row[0] for row in rows}


def _make_pending(row: dict) -> PendingRetirement:
    row.setdefault("plex_added_at", "")
    row.setdefault("stage_days", 0)
    row.setdefault("action", "redownload")
    row.setdefault("archived_path", "")
    return PendingRetirement(**row)


async def get_pending_downgrades(status: str = "pending") -> list[PendingRetirement]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_downgrades WHERE status=? ORDER BY scheduled_for ASC",
            (status,)
        ) as cursor:
            return [_make_pending(dict(r)) for r in await cursor.fetchall()]


async def get_exclusions() -> list[PendingRetirement]:
    """Return all excluded movies (status='excluded'), newest first."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pending_downgrades WHERE status='excluded' ORDER BY queued_at DESC"
        ) as cursor:
            return [_make_pending(dict(r)) for r in await cursor.fetchall()]


async def get_due_downgrades() -> list[PendingRetirement]:
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


async def get_rss_cache(url: str) -> RSSCacheEntry | None:
    """Return cached ETag/Last-Modified for a RSS URL, or None if not yet seen."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT url, etag, last_modified, last_fetched FROM rss_cache WHERE url=?",
            (url,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return RSSCacheEntry(**dict(row))


async def get_task_schedules() -> list[TaskSchedule]:
    """Return all task schedules ordered by task_id."""
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT task_id, task_name, interval_minutes, last_run, enabled FROM task_schedule ORDER BY task_id"
        ) as cursor:
            rows = await cursor.fetchall()
            return [TaskSchedule(
                task_id=row["task_id"],
                task_name=row["task_name"],
                interval_minutes=row["interval_minutes"],
                last_run=row["last_run"],
                enabled=bool(row["enabled"]),
            ) for row in rows]


async def get_task_schedule(task_id: str) -> TaskSchedule | None:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT task_id, task_name, interval_minutes, last_run, enabled FROM task_schedule WHERE task_id=?",
            (task_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return TaskSchedule(
                task_id=row["task_id"],
                task_name=row["task_name"],
                interval_minutes=row["interval_minutes"],
                last_run=row["last_run"],
                enabled=bool(row["enabled"]),
            )


async def is_task_due(task_id: str) -> bool:
    """Return True if the task has never run or its interval has elapsed."""
    task = await get_task_schedule(task_id)
    if task is None or not task.enabled:
        return False
    if task.last_run is None:
        return True
    from datetime import timedelta
    last = datetime.fromisoformat(task.last_run)
    return datetime.utcnow() >= last + timedelta(minutes=task.interval_minutes)


async def mark_task_run(task_id: str) -> None:
    """Record that a task just ran successfully."""
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE task_schedule SET last_run=? WHERE task_id=?",
            (datetime.utcnow().isoformat(), task_id)
        )
        await db.commit()


async def set_task_interval(task_id: str, interval_minutes: int) -> None:
    """Update how often a task runs (in minutes)."""
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE task_schedule SET interval_minutes=? WHERE task_id=?",
            (interval_minutes, task_id)
        )
        await db.commit()


async def set_task_enabled(task_id: str, enabled: bool) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE task_schedule SET enabled=? WHERE task_id=?",
            (1 if enabled else 0, task_id)
        )
        await db.commit()


async def set_rss_cache(url: str, etag: str | None, last_modified: str | None) -> None:
    """Upsert ETag/Last-Modified for a RSS URL after a successful 200 response."""
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO rss_cache (url, etag, last_modified, last_fetched)
               VALUES (?,?,?,?)
               ON CONFLICT(url) DO UPDATE SET
                 etag=excluded.etag,
                 last_modified=excluded.last_modified,
                 last_fetched=excluded.last_fetched""",
            (url, etag, last_modified, datetime.utcnow().isoformat())
        )
        await db.commit()
