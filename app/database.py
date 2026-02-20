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
    action: str  # updated, added, skipped, error
    details: str


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
        await db.commit()


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
