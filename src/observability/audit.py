from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from threading import Lock

logger = logging.getLogger(__name__)

_TABLE = "admin_audit"
_LOCK = Lock()


def _db_path() -> str:
    return os.environ.get("AUDIT_DB", "./audit.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False, timeout=10)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id TEXT PRIMARY KEY,
            ts REAL NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT,
            client_ip TEXT,
            details TEXT
        )
        """
    )
    return conn


def record_admin_action(
    actor: str,
    action: str,
    target: str | None = None,
    client_ip: str | None = None,
    details: dict | None = None,
) -> None:
    """Persist one state-changing or data-viewing admin action — answers "who
    did what, to which account, when, and from where" for FERPA-style access
    review. Auditing must never break the action it records: any failure here
    is logged and swallowed rather than raised.
    """
    try:
        with _LOCK:
            conn = _connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} (id, ts, actor, action, target, client_ip, details) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        time.time(),
                        actor,
                        action,
                        target,
                        client_ip,
                        json.dumps(details) if details else None,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        logger.exception("Failed to write admin audit record: actor=%s action=%s target=%s", actor, action, target)
        return

    logger.info("admin action: actor=%s action=%s target=%s client_ip=%s", actor, action, target, client_ip)


def recent_admin_actions(limit: int = 100) -> list[dict]:
    """Most recent admin audit records, most recent first. Returns an empty
    list (rather than raising) if the audit log is unavailable."""
    try:
        with _LOCK:
            conn = _connect()
            try:
                rows = conn.execute(
                    f"SELECT id, ts, actor, action, target, client_ip, details "
                    f"FROM {_TABLE} ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            finally:
                conn.close()
    except Exception:
        logger.exception("Failed to read admin audit records")
        return []

    return [
        {
            "id": r[0],
            "ts": r[1],
            "actor": r[2],
            "action": r[3],
            "target": r[4],
            "client_ip": r[5],
            "details": json.loads(r[6]) if r[6] else None,
        }
        for r in rows
    ]
