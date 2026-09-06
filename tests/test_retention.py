import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.retention import purge_expired_threads, record_activity


def _last_seen(db_path: str, thread_id: str) -> float | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_seen FROM thread_activity WHERE thread_id = ?", (thread_id,)
        ).fetchone()
    return row[0] if row else None


class TestRecordActivity:
    def test_writes_a_row_for_new_thread(self, monkeypatch, tmp_path):
        db_path = str(tmp_path / "chat.db")
        monkeypatch.setenv("CHAT_DB", db_path)
        record_activity("user-1")
        assert _last_seen(db_path, "user-1") is not None

    def test_repeated_call_updates_last_seen(self, monkeypatch, tmp_path):
        db_path = str(tmp_path / "chat.db")
        monkeypatch.setenv("CHAT_DB", db_path)
        record_activity("user-1")
        first = _last_seen(db_path, "user-1")
        time.sleep(0.01)
        record_activity("user-1")
        second = _last_seen(db_path, "user-1")
        assert second > first

    def test_failure_is_swallowed_not_raised(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CHAT_DB", str(tmp_path / "nonexistent_dir" / "chat.db"))
        record_activity("user-1")  # must not raise


class TestPurgeExpiredThreads:
    def test_disabled_by_default_returns_empty(self, monkeypatch):
        monkeypatch.delenv("RETENTION_DAYS", raising=False)
        result = purge_expired_threads()
        assert result.threads_purged == []

    def test_zero_retention_days_returns_empty(self):
        result = purge_expired_threads(retention_days=0)
        assert result.threads_purged == []

    def test_purges_stale_thread_and_leaves_recent_one(self, monkeypatch, tmp_path):
        db_path = str(tmp_path / "chat.db")
        monkeypatch.setenv("CHAT_DB", db_path)

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE thread_activity (thread_id TEXT PRIMARY KEY, last_seen REAL NOT NULL)"
            )
            stale_ts = time.time() - 40 * 86400
            recent_ts = time.time()
            conn.execute("INSERT INTO thread_activity VALUES (?, ?)", ("stale-user", stale_ts))
            conn.execute("INSERT INTO thread_activity VALUES (?, ?)", ("recent-user", recent_ts))
            conn.commit()

        cleared: list[str] = []
        result = purge_expired_threads(retention_days=30, clear_thread=cleared.append)

        assert result.threads_purged == ["stale-user"]
        assert cleared == ["stale-user"]
        assert _last_seen(db_path, "stale-user") is None
        assert _last_seen(db_path, "recent-user") is not None

    def test_clear_thread_failure_leaves_activity_row_for_retry(self, monkeypatch, tmp_path):
        db_path = str(tmp_path / "chat.db")
        monkeypatch.setenv("CHAT_DB", db_path)

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE thread_activity (thread_id TEXT PRIMARY KEY, last_seen REAL NOT NULL)"
            )
            conn.execute(
                "INSERT INTO thread_activity VALUES (?, ?)", ("broken-user", time.time() - 40 * 86400)
            )
            conn.commit()

        def _raise(thread_id):
            raise RuntimeError("boom")

        result = purge_expired_threads(retention_days=30, clear_thread=_raise)

        assert result.threads_purged == ["broken-user"]
        assert _last_seen(db_path, "broken-user") is not None
