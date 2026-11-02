"""SQLite-каталог игр и состояние почасового обхода."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Novosibirsk")
STALE_LOCK = timedelta(minutes=30)

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    path TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    cover TEXT NOT NULL,
    developer TEXT NOT NULL,
    description TEXT NOT NULL,
    video TEXT NOT NULL,
    critic_summary TEXT NOT NULL,
    user_summary TEXT NOT NULL,
    processed_on TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_scores (
    path TEXT NOT NULL,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    metascore REAL,
    userscore REAL,
    PRIMARY KEY (path, position),
    FOREIGN KEY (path) REFERENCES games(path)
);

CREATE TABLE IF NOT EXISTS critic_quotes (
    path TEXT NOT NULL,
    position INTEGER NOT NULL,
    quote TEXT NOT NULL,
    score REAL,
    source TEXT NOT NULL,
    platform TEXT NOT NULL,
    PRIMARY KEY (path, position),
    FOREIGN KEY (path) REFERENCES games(path)
);

CREATE TABLE IF NOT EXISTS user_quotes (
    path TEXT NOT NULL,
    position INTEGER NOT NULL,
    quote TEXT NOT NULL,
    score REAL,
    source TEXT NOT NULL,
    platform TEXT NOT NULL,
    PRIMARY KEY (path, position),
    FOREIGN KEY (path) REFERENCES games(path)
);

CREATE TABLE IF NOT EXISTS crawl_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    day TEXT,
    phase TEXT NOT NULL,
    new_page INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    source_url TEXT,
    page INTEGER,
    taken INTEGER NOT NULL DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS lets_plays (
    path TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    views INTEGER NOT NULL,
    kind TEXT NOT NULL,
    matched_words TEXT NOT NULL,
    transcript TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (path) REFERENCES games(path)
);

CREATE TABLE IF NOT EXISTS crawl_lock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS translations (
    path TEXT NOT NULL,
    field TEXT NOT NULL,
    source TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (path, field)
);
"""


class AlreadyRunning(Exception):
    def __init__(self):
        super().__init__("Обход уже выполняется")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def local_day(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("Время запуска должно быть с часовым поясом")
    return now.astimezone(ZONE).date().isoformat()


def read_state(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT day, phase, new_page FROM crawl_state WHERE id = 1"
    ).fetchone()


def write_state(conn: sqlite3.Connection, day: str, phase: str, new_page: int) -> None:
    conn.execute(
        """
        INSERT INTO crawl_state (id, day, phase, new_page)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            day = excluded.day,
            phase = excluded.phase,
            new_page = excluded.new_page
        """,
        (day, phase, new_page),
    )
    conn.commit()


def load_catalog(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT path, title, cover, developer, description, video,
               critic_summary, user_summary
        FROM games
        ORDER BY title, path
        """
    ).fetchall()
    grouped: dict[str, list[dict]] = {}
    for row in conn.execute(
        """
        SELECT path, position, name, metascore, userscore
        FROM platform_scores
        ORDER BY position
        """
    ):
        grouped.setdefault(row["path"], []).append(
            {
                "name": row["name"],
                "metascore": row["metascore"],
                "userscore": row["userscore"],
            }
        )
    return [_catalog_game(row, grouped.get(row["path"], [])) for row in rows]


def load_letsplay(conn: sqlite3.Connection, path: str) -> dict | None:
    row = conn.execute(
        """
        SELECT video_id, title, url, views, kind, matched_words, transcript, conclusion
        FROM lets_plays
        WHERE path = ?
        """,
        (path,),
    ).fetchone()
    if row is None:
        return None
    words = json.loads(row["matched_words"])
    return {
        "video_id": row["video_id"],
        "title": row["title"],
        "url": row["url"],
        "views": row["views"],
        "kind": row["kind"],
        "words": words if isinstance(words, list) else [],
        "transcript": row["transcript"],
        "conclusion": row["conclusion"],
    }


def crawl_snapshot(conn: sqlite3.Connection) -> dict:
    lock = conn.execute("SELECT started_at FROM crawl_lock WHERE id = 1").fetchone()
    run = conn.execute(
        """
        SELECT trigger, started_at, finished_at, status, source_url, page, taken, message
        FROM crawl_runs
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    state = read_state(conn)
    return {
        "lock": lock["started_at"] if lock else None,
        "run": dict(run) if run else None,
        "state": dict(state) if state else None,
    }


def _catalog_game(row: sqlite3.Row, platforms: list[dict]) -> dict:
    developer = json.loads(row["developer"])
    return {
        "path": row["path"],
        "title": row["title"],
        "cover": row["cover"],
        "developer": developer if isinstance(developer, list) else [],
        "description": row["description"],
        "video": row["video"],
        "critic_summary": row["critic_summary"],
        "user_summary": row["user_summary"],
        "platforms": platforms,
    }


def list_games(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT path, title, developer, description FROM games ORDER BY title, path"
    ).fetchall()
    games = []
    for row in rows:
        developer = json.loads(row["developer"])
        games.append(
            {
                "path": row["path"],
                "title": row["title"],
                "developer": developer if isinstance(developer, list) else [],
                "description": row["description"],
            }
        )
    return games


def processed_today(conn: sqlite3.Connection, path: str, day: str) -> bool:
    row = conn.execute("SELECT processed_on FROM games WHERE path = ?", (path,)).fetchone()
    return row is not None and row["processed_on"] == day


def save_game(
    conn: sqlite3.Connection,
    path: str,
    game: dict,
    day: str,
    updated_at: str,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO games (
                path, title, cover, developer, description, video,
                critic_summary, user_summary, processed_on, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, '', '', ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                title = excluded.title,
                cover = excluded.cover,
                developer = excluded.developer,
                description = excluded.description,
                video = excluded.video,
                processed_on = excluded.processed_on,
                updated_at = excluded.updated_at
            """,
            (
                path,
                game["title"],
                game["cover"],
                json.dumps(game["developer"], ensure_ascii=False),
                game["description"],
                game["video"],
                day,
                updated_at,
            ),
        )
        conn.execute("DELETE FROM platform_scores WHERE path = ?", (path,))
        conn.execute("DELETE FROM critic_quotes WHERE path = ?", (path,))
        conn.execute("DELETE FROM user_quotes WHERE path = ?", (path,))
        for position, platform in enumerate(game["platforms"]):
            conn.execute(
                """
                INSERT INTO platform_scores (path, position, name, metascore, userscore)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    path,
                    position,
                    platform["name"],
                    platform["metascore"],
                    platform["userscore"],
                ),
            )
        _insert_quotes(conn, "critic_quotes", path, game["critic_quotes"])
        _insert_quotes(conn, "user_quotes", path, game["user_quotes"])


def save_summary(
    conn: sqlite3.Connection,
    path: str,
    column: str,
    text: str,
    updated_at: str,
) -> None:
    if column not in ("critic_summary", "user_summary"):
        raise ValueError(column)
    conn.execute(
        f"UPDATE games SET {column} = ?, updated_at = ? WHERE path = ?",
        (text, updated_at, path),
    )
    conn.commit()


def save_letsplay(conn: sqlite3.Connection, path: str, video: dict, updated_at: str) -> None:
    conn.execute(
        """
        INSERT INTO lets_plays (
            path, video_id, title, url, views, kind, matched_words,
            transcript, conclusion, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            video_id = excluded.video_id,
            title = excluded.title,
            url = excluded.url,
            views = excluded.views,
            kind = excluded.kind,
            matched_words = excluded.matched_words,
            transcript = excluded.transcript,
            conclusion = excluded.conclusion,
            updated_at = excluded.updated_at
        """,
        (
            path,
            video["video_id"],
            video["title"],
            video["url"],
            video["views"],
            video["kind"],
            json.dumps(video["words"], ensure_ascii=False),
            video.get("transcript") or "",
            video.get("conclusion") or "",
            updated_at,
        ),
    )
    conn.commit()


_TRANSLATION_FIELDS = {
    "description",
    "critic_summary",
    "user_summary",
    "conclusion",
    "transcript",
}


def load_translation(
    conn: sqlite3.Connection, path: str, field: str, source: str
) -> str | None:
    if field not in _TRANSLATION_FIELDS:
        raise ValueError(field)
    row = conn.execute(
        "SELECT source, text FROM translations WHERE path = ? AND field = ?",
        (path, field),
    ).fetchone()
    if row is None or row["source"] != source:
        return None
    return row["text"]


def save_translation(
    conn: sqlite3.Connection, path: str, field: str, source: str, text: str
) -> None:
    if field not in _TRANSLATION_FIELDS:
        raise ValueError(field)
    conn.execute(
        """
        INSERT INTO translations (path, field, source, text)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(path, field) DO UPDATE SET
            source = excluded.source,
            text = excluded.text
        """,
        (path, field, source, text),
    )
    conn.commit()


def clear_letsplay(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("DELETE FROM lets_plays WHERE path = ?", (path,))
    conn.commit()


def _insert_quotes(conn: sqlite3.Connection, table: str, path: str, quotes: list[dict]) -> None:
    for position, quote in enumerate(quotes):
        conn.execute(
            f"""
            INSERT INTO {table} (path, position, quote, score, source, platform)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                path,
                position,
                quote["quote"],
                quote["score"],
                quote["source"],
                quote["platform"],
            ),
        )


def acquire_lock(conn: sqlite3.Connection, now: datetime) -> None:
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT started_at FROM crawl_lock WHERE id = 1").fetchone()
    if row is not None:
        started = datetime.fromisoformat(row["started_at"])
        if now - started < STALE_LOCK:
            conn.rollback()
            raise AlreadyRunning()
        conn.execute("DELETE FROM crawl_lock WHERE id = 1")
    conn.execute(
        "INSERT INTO crawl_lock (id, started_at) VALUES (1, ?)",
        (now.isoformat(),),
    )
    conn.commit()


def release_lock(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM crawl_lock WHERE id = 1")
    conn.commit()


def start_run(conn: sqlite3.Connection, trigger: str, started_at: str, url: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO crawl_runs (trigger, started_at, status, source_url, taken)
        VALUES (?, ?, 'running', ?, 0)
        """,
        (trigger, started_at, url),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    finished_at: str,
    taken: int,
    page: int | None,
    message: str,
) -> None:
    conn.execute(
        """
        UPDATE crawl_runs
        SET status = ?, finished_at = ?, taken = ?, page = ?, message = ?
        WHERE id = ?
        """,
        (status, finished_at, taken, page, message, run_id),
    )
    conn.commit()
