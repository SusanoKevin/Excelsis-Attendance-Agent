from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable

try:
    import psycopg
except ImportError:  # pragma: no cover - postgres checkpointing is optional
    psycopg = None

logger = logging.getLogger(__name__)

_ACTIVITY_TABLE = "thread_activity"


def _checkpoint_db_uri() -> str:
    return os.environ.get("CHECKPOINT_DB_URI", "")


def record_activity(thread_id: str) -> None:
    """Records that `thread_id` was just used, so the retention purge job
    knows which threads are still active. Called on every agent query.
    Best-effort: a failure here must never break a chat request — the same
    contract as the trace recorder's Redis writes.
    """
    try:
        if _checkpoint_db_uri():
            _record_activity_postgres(thread_id)
        else:
            _record_activity_sqlite(thread_id)
    except Exception:
        logger.exception("Failed to record thread activity for retention tracking: thread_id=%s", thread_id)


def _record_activity_sqlite(thread_id: str) -> None:
    db_path = os.getenv("CHAT_DB", "./chat.db")
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_ACTIVITY_TABLE} "
            "(thread_id TEXT PRIMARY KEY, last_seen REAL NOT NULL)"
        )
        conn.execute(
            f"INSERT INTO {_ACTIVITY_TABLE} (thread_id, last_seen) VALUES (?, ?) "
            "ON CONFLICT(thread_id) DO UPDATE SET last_seen = excluded.last_seen",
            (thread_id, time.time()),
        )
        conn.commit()


def _record_activity_postgres(thread_id: str) -> None:
    with psycopg.connect(_checkpoint_db_uri(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {_ACTIVITY_TABLE} "
                "(thread_id TEXT PRIMARY KEY, last_seen DOUBLE PRECISION NOT NULL)"
            )
            cur.execute(
                f"INSERT INTO {_ACTIVITY_TABLE} (thread_id, last_seen) VALUES (%s, %s) "
                "ON CONFLICT (thread_id) DO UPDATE SET last_seen = EXCLUDED.last_seen",
                (thread_id, time.time()),
            )


@dataclass(frozen=True)
class PurgeResult:
    retention_days: int
    threads_purged: list[str]


def purge_expired_threads(
    retention_days: int | None = None,
    clear_thread: Callable[[str], None] | None = None,
) -> PurgeResult:
    """Delete checkpoint history for threads inactive past the retention
    window — FERPA data-minimization for conversations that hold copies of
    student data. Disabled by default (RETENTION_DAYS unset or 0).

    `clear_thread` deletes the checkpoint rows for one thread — pass
    ExcelsisAgent's own `_clear_thread` so the purge uses the exact same
    table-deletion logic as corruption recovery. Idempotent and safe to run
    repeatedly.
    """
    days = retention_days if retention_days is not None else int(os.environ.get("RETENTION_DAYS", "0"))
    if days <= 0:
        return PurgeResult(days, [])

    cutoff = time.time() - days * 86400
    stale = _find_stale_threads(cutoff)

    for thread_id in stale:
        try:
            if clear_thread is not None:
                clear_thread(thread_id)
            _delete_activity_row(thread_id)
        except Exception:
            logger.exception("Failed to purge thread_id=%s during retention sweep", thread_id)

    logger.info("Retention purge complete: retention_days=%d threads_purged=%d", days, len(stale))
    return PurgeResult(days, stale)


def _find_stale_threads(cutoff: float) -> list[str]:
    if _checkpoint_db_uri():
        with psycopg.connect(_checkpoint_db_uri(), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {_ACTIVITY_TABLE} "
                    "(thread_id TEXT PRIMARY KEY, last_seen DOUBLE PRECISION NOT NULL)"
                )
                cur.execute(f"SELECT thread_id FROM {_ACTIVITY_TABLE} WHERE last_seen < %s", (cutoff,))
                return [row[0] for row in cur.fetchall()]

    db_path = os.getenv("CHAT_DB", "./chat.db")
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_ACTIVITY_TABLE} "
            "(thread_id TEXT PRIMARY KEY, last_seen REAL NOT NULL)"
        )
        rows = conn.execute(f"SELECT thread_id FROM {_ACTIVITY_TABLE} WHERE last_seen < ?", (cutoff,)).fetchall()
        return [r[0] for r in rows]


def _delete_activity_row(thread_id: str) -> None:
    if _checkpoint_db_uri():
        with psycopg.connect(_checkpoint_db_uri(), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {_ACTIVITY_TABLE} WHERE thread_id = %s", (thread_id,))
        return

    db_path = os.getenv("CHAT_DB", "./chat.db")
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute(f"DELETE FROM {_ACTIVITY_TABLE} WHERE thread_id = ?", (thread_id,))
        conn.commit()
