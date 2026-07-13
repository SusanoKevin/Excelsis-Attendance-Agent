import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.sql_store import SQLDataStore


class TestQuoteIdentifier:
    def test_bare_identifier(self):
        assert SQLDataStore._q("UserId") == "[UserId]"

    def test_qualified_identifier(self):
        assert SQLDataStore._q("a.UserId") == "[a].[UserId]"

    def test_bracket_in_name_escaped(self):
        assert SQLDataStore._q("weird]name") == "[weird]]name]"


class TestPrimaryTableExpression:
    def test_unconfigured_raises_with_fallback_hint(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_TABLE", "")
        store = SQLDataStore()
        with pytest.raises(RuntimeError, match="retrieve_schema"):
            store._table_expr()

    def test_bare_table_name_gets_quoted(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_TABLE", "Attendance")
        store = SQLDataStore()
        assert store._table_expr() == "[Attendance]"

    def test_join_expression_passed_through_unquoted(self, monkeypatch):
        expr = "SIS_Attendance a JOIN UserInfo ui ON a.UserId = ui.UserId"
        monkeypatch.setenv("PRIMARY_TABLE", expr)
        store = SQLDataStore()
        assert store._table_expr() == expr

    def test_semicolon_in_expression_rejected(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_TABLE", "Attendance a JOIN Users u ON a.Id=u.Id; DROP TABLE Users")
        store = SQLDataStore()
        with pytest.raises(RuntimeError, match="semicolon"):
            store._table_expr()


class TestPrimaryTableFallback:
    """When a query against PRIMARY_TABLE fails (wrong table/column, bad join),
    structured tools must surface a clear fallback hint instead of a raw DB
    error, so the agent can retry with retrieve_schema + run_sql_query."""

    def test_summary_wraps_query_failure(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_TABLE", "NoSuchTable")
        store = SQLDataStore()
        monkeypatch.setattr(store, "_exec", lambda *a, **k: (_ for _ in ()).throw(
            Exception("Invalid object name 'NoSuchTable'")
        ))
        with pytest.raises(RuntimeError, match="run_sql_query"):
            store.summary()

    def test_compute_stats_wraps_query_failure(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_TABLE", "Attendance")
        store = SQLDataStore()
        monkeypatch.setattr(store, "_exec", lambda *a, **k: (_ for _ in ()).throw(
            Exception("Invalid column name 'status'")
        ))
        with pytest.raises(RuntimeError, match="retrieve_schema"):
            store.compute_stats()

    def test_get_threshold_alerts_wraps_query_failure(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_TABLE", "Attendance")
        store = SQLDataStore()
        monkeypatch.setattr(store, "_exec", lambda *a, **k: (_ for _ in ()).throw(
            Exception("Invalid column name 'entity_name'")
        ))
        with pytest.raises(RuntimeError, match="retrieve_schema"):
            store.get_threshold_alerts()

    def test_entity_weekly_rates_wraps_query_failure(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_TABLE", "Attendance")
        store = SQLDataStore()
        monkeypatch.setattr(store, "_exec", lambda *a, **k: (_ for _ in ()).throw(
            Exception("Invalid object name 'Attendance'")
        ))
        with pytest.raises(RuntimeError, match="retrieve_schema"):
            store.entity_weekly_rates(["1", "2"])

    def test_permission_error_not_masked(self, monkeypatch):
        """A PermissionError (e.g. blocked database) should propagate as-is,
        not get relabeled as a PRIMARY_TABLE misconfiguration."""
        monkeypatch.setenv("PRIMARY_TABLE", "Attendance")
        store = SQLDataStore()
        monkeypatch.setattr(store, "_exec", lambda *a, **k: (_ for _ in ()).throw(
            PermissionError("Database 'other' is not in the configured allowlist.")
        ))
        with pytest.raises(PermissionError, match="allowlist"):
            store.summary()
