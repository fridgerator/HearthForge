"""
SQLite database layer for HearthForge.

Replaces the JSON file storage for:
  - User authentication (was ~/.hearthforge/users.json)
  - User memory: history, preferences, tool knowledge (was ~/.hearthforge/memory/{user_id}.json)
  - Job persistence (was in-memory dict, lost on restart)

Single file: ~/.hearthforge/hearthforge.db

Thread/async safety: SQLite in WAL mode with proper connection handling.
Each operation opens its own connection (short-lived), so concurrent
asyncio tasks won't block each other for long.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from config import DATABASE_PATH

logger = logging.getLogger(__name__)


def _get_conn() -> sqlite3.Connection:
    """Get a new SQLite connection with standard settings."""
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create all tables if they don't exist. Safe to call multiple times."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username    TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                display_name  TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                goal            TEXT NOT NULL,
                outcome         TEXT NOT NULL,
                summary         TEXT NOT NULL,
                task_count      INTEGER NOT NULL,
                completed_count INTEGER NOT NULL,
                failed_count    INTEGER NOT NULL,
                elapsed_seconds REAL NOT NULL,
                workspace_id    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_history_user
                ON memory_history(user_id);

            CREATE TABLE IF NOT EXISTS memory_preferences (
                user_id    TEXT NOT NULL,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                source     TEXT NOT NULL DEFAULT 'explicit',
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, key)
            );

            CREATE TABLE IF NOT EXISTS memory_tool_knowledge (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                source      TEXT NOT NULL,
                fact        TEXT NOT NULL,
                learned_at  TEXT NOT NULL,
                confidence  TEXT NOT NULL DEFAULT 'confirmed',
                UNIQUE(user_id, source, fact)
            );
            CREATE INDEX IF NOT EXISTS idx_tool_knowledge_user
                ON memory_tool_knowledge(user_id);

            CREATE TABLE IF NOT EXISTS jobs (
                job_id       TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL,
                goal         TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL,
                completed_at TEXT,
                result_json  TEXT,
                error        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_user
                ON jobs(user_id);
        """)
        conn.commit()
        logger.info(f"Database initialized at {DATABASE_PATH}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def create_user(username: str, password_hash: str, display_name: str) -> bool:
    """Insert a new user. Returns False if username already taken."""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, display_name, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, display_name, datetime.now().isoformat()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_user(username: str) -> dict | None:
    """Get a user row as dict, or None."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def has_users() -> bool:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None
    finally:
        conn.close()


def list_users() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT username, display_name, created_at FROM users").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Memory — History
# ---------------------------------------------------------------------------

def add_history(user_id: str, *, timestamp: str, goal: str, outcome: str,
                summary: str, task_count: int, completed_count: int,
                failed_count: int, elapsed_seconds: float,
                workspace_id: str | None = None,
                max_entries: int = 50) -> None:
    """Append a run to history, trimming oldest if over max_entries."""
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO memory_history
               (user_id, timestamp, goal, outcome, summary, task_count,
                completed_count, failed_count, elapsed_seconds, workspace_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, timestamp, goal, outcome, summary, task_count,
             completed_count, failed_count, elapsed_seconds, workspace_id),
        )
        # Trim oldest entries beyond max_entries
        conn.execute(
            """DELETE FROM memory_history
               WHERE user_id = ? AND id NOT IN (
                   SELECT id FROM memory_history
                   WHERE user_id = ?
                   ORDER BY id DESC LIMIT ?
               )""",
            (user_id, user_id, max_entries),
        )
        conn.commit()
    finally:
        conn.close()


def get_history(user_id: str, limit: int = 50) -> list[dict]:
    """Get recent history entries, newest first."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM memory_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Memory — Preferences
# ---------------------------------------------------------------------------

def set_preference(user_id: str, key: str, value: str, source: str = "explicit") -> None:
    """Upsert a preference."""
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO memory_preferences (user_id, key, value, source, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, source=excluded.source""",
            (user_id, key, value, source, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_preference(user_id: str, key: str) -> str | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM memory_preferences WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def get_preferences(user_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT key, value, source, created_at FROM memory_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def remove_preference(user_id: str, key: str) -> bool:
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM memory_preferences WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Memory — Tool Knowledge
# ---------------------------------------------------------------------------

def add_tool_knowledge(user_id: str, source: str, fact: str,
                       confidence: str = "confirmed") -> bool:
    """Add a tool knowledge entry. Returns False if duplicate."""
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO memory_tool_knowledge (user_id, source, fact, learned_at, confidence)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, source, fact, datetime.now().isoformat(), confidence),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_tool_knowledge(user_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT source, fact, learned_at, confidence FROM memory_tool_knowledge WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def remove_tool_knowledge_by_source(user_id: str, source: str) -> int:
    """Delete all entries for a source. Returns count deleted."""
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM memory_tool_knowledge WHERE user_id = ? AND source = ?",
            (user_id, source),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def create_job(job_id: str, user_id: str, goal: str, status: str = "pending") -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO jobs (job_id, user_id, goal, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, user_id, goal, status, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def update_job(job_id: str, *, status: str | None = None,
               completed_at: str | None = None,
               result_json: str | None = None,
               error: str | None = None) -> None:
    """Update fields on a job. Only non-None values are updated."""
    sets = []
    params = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if completed_at is not None:
        sets.append("completed_at = ?")
        params.append(completed_at)
    if result_json is not None:
        sets.append("result_json = ?")
        params.append(result_json)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if not sets:
        return
    params.append(job_id)
    conn = _get_conn()
    try:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", params)
        conn.commit()
    finally:
        conn.close()


def get_job(job_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_jobs_for_user(user_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration — import existing JSON data
# ---------------------------------------------------------------------------

def migrate_from_json(memory_dir: Path, auth_db_path: Path) -> dict[str, int]:
    """
    One-time import of existing JSON files into SQLite.
    Returns counts of migrated items.

    Safe to run multiple times — uses INSERT OR IGNORE to skip duplicates.
    """
    counts = {"users": 0, "history": 0, "preferences": 0, "tool_knowledge": 0}

    # Migrate users.json
    if auth_db_path.exists():
        try:
            users_data = json.loads(auth_db_path.read_text())
            conn = _get_conn()
            try:
                for username, data in users_data.items():
                    try:
                        conn.execute(
                            """INSERT OR IGNORE INTO users
                               (username, password_hash, display_name, created_at)
                               VALUES (?, ?, ?, ?)""",
                            (username, data["password_hash"],
                             data.get("display_name", username),
                             data.get("created_at", datetime.now().isoformat())),
                        )
                        counts["users"] += 1
                    except sqlite3.IntegrityError:
                        pass
                conn.commit()
            finally:
                conn.close()
            logger.info(f"Migrated {counts['users']} users from {auth_db_path}")
        except Exception as e:
            logger.warning(f"Failed to migrate users.json: {e}")

    # Migrate memory files
    if memory_dir.exists():
        for mem_file in memory_dir.glob("*.json"):
            user_id = mem_file.stem
            try:
                data = json.loads(mem_file.read_text())
                conn = _get_conn()
                try:
                    # History
                    for h in data.get("history", []):
                        try:
                            conn.execute(
                                """INSERT INTO memory_history
                                   (user_id, timestamp, goal, outcome, summary,
                                    task_count, completed_count, failed_count,
                                    elapsed_seconds, workspace_id)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (user_id, h["timestamp"], h["goal"], h["outcome"],
                                 h["summary"], h["task_count"], h["completed_count"],
                                 h["failed_count"], h["elapsed_seconds"],
                                 h.get("workspace_id")),
                            )
                            counts["history"] += 1
                        except Exception:
                            pass

                    # Preferences
                    for p in data.get("preferences", []):
                        try:
                            conn.execute(
                                """INSERT OR IGNORE INTO memory_preferences
                                   (user_id, key, value, source, created_at)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (user_id, p["key"], p["value"],
                                 p.get("source", "explicit"),
                                 p.get("created_at", datetime.now().isoformat())),
                            )
                            counts["preferences"] += 1
                        except Exception:
                            pass

                    # Tool knowledge
                    for tk in data.get("tool_knowledge", []):
                        try:
                            conn.execute(
                                """INSERT OR IGNORE INTO memory_tool_knowledge
                                   (user_id, source, fact, learned_at, confidence)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (user_id, tk["source"], tk["fact"],
                                 tk["learned_at"],
                                 tk.get("confidence", "confirmed")),
                            )
                            counts["tool_knowledge"] += 1
                        except Exception:
                            pass

                    conn.commit()
                finally:
                    conn.close()
                logger.info(f"Migrated memory for user '{user_id}' from {mem_file}")
            except Exception as e:
                logger.warning(f"Failed to migrate memory file {mem_file}: {e}")

    return counts
