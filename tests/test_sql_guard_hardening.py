import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import src.sql_store as sql_store_module
from src.sql_store import _apply_lock_timeout, _assert_select_only, _set_lock_timeout


class TestBlocklist:
    """_assert_select_only must reject dangerous T-SQL constructs even when
    they appear inside an otherwise syntactically valid SELECT."""

    def test_xp_cmdshell_blocked(self):
        with pytest.raises(PermissionError, match="blocked"):
            _assert_select_only("SELECT * FROM OPENROWSET('SQLNCLI', 'xp_cmdshell ''dir''')")

    def test_openquery_blocked(self):
        with pytest.raises(PermissionError, match="blocked"):
            _assert_select_only("SELECT * FROM OPENQUERY(LinkedServer, 'SELECT 1')")

    def test_opendatasource_blocked(self):
        with pytest.raises(PermissionError, match="blocked"):
            _assert_select_only(
                "SELECT * FROM OPENDATASOURCE('SQLNCLI', 'Data Source=x').db.dbo.t"
            )

    def test_bulk_insert_blocked(self):
        with pytest.raises(PermissionError, match="blocked"):
            _assert_select_only("SELECT 1 WHERE 1=1; BULK INSERT t FROM 'c:\\x.csv'")

    def test_waitfor_delay_blocked(self):
        with pytest.raises(PermissionError, match="blocked"):
            _assert_select_only("SELECT CASE WHEN 1=1 THEN 1 ELSE (WAITFOR DELAY '00:00:05') END")

    def test_sp_execute_blocked(self):
        with pytest.raises(PermissionError, match="blocked"):
            _assert_select_only("SELECT 1 FROM (SELECT sp_executesql N'SELECT 1') t")

    def test_ordinary_select_not_blocked_by_blocklist(self):
        _assert_select_only("SELECT TOP 10 UserId, Status FROM Attendance")


class TestSelectIntoBlocked:
    def test_select_into_new_table_blocked(self):
        with pytest.raises(PermissionError, match="INTO"):
            _assert_select_only("SELECT * INTO NewTable FROM Attendance")

    def test_select_into_temp_table_blocked(self):
        with pytest.raises(PermissionError, match="INTO"):
            _assert_select_only("SELECT UserId INTO #temp FROM Attendance")


class _FakeCursor:
    def __init__(self, executed: list) -> None:
        self._executed = executed

    def execute(self, sql):
        self._executed.append(sql)

    def close(self):
        pass


class _FakeDbapiConnection:
    def __init__(self) -> None:
        self.executed: list = []
        self.committed = False

    def cursor(self):
        return _FakeCursor(self.executed)

    def commit(self):
        self.committed = True


class TestLockTimeout:
    def test_set_lock_timeout_issues_expected_statement(self):
        conn = _FakeDbapiConnection()
        _set_lock_timeout(conn, 5000)
        assert conn.executed == ["SET LOCK_TIMEOUT 5000"]
        assert conn.committed is True

    def test_apply_lock_timeout_uses_env_var(self, monkeypatch):
        from sqlalchemy import create_engine

        monkeypatch.setenv("SQL_LOCK_TIMEOUT_MS", "1234")
        calls: list[int] = []
        monkeypatch.setattr(sql_store_module, "_set_lock_timeout", lambda conn, ms: calls.append(ms))

        engine = create_engine("sqlite://")
        _apply_lock_timeout(engine)

        with engine.connect():
            pass

        assert calls == [1234]
