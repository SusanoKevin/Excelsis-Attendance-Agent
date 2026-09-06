import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest

from src.identifier_guard import SchemaCatalog, UnknownIdentifierError, validate_identifiers
from src.sql_store import SQLDataStore


class TestSchemaCatalog:
    def test_from_rows_is_case_insensitive(self):
        catalog = SchemaCatalog.from_rows([("dbo.Attendance", ["UserId", "Status"])])
        assert catalog.tables == {"attendance": {"userid", "status"}}

    def test_bracket_quoted_names_stripped(self):
        catalog = SchemaCatalog.from_rows([("[dbo].[Attendance]", ["[UserId]"])])
        assert catalog.tables == {"attendance": {"userid"}}

    def test_empty_catalog_is_empty(self):
        assert SchemaCatalog().is_empty is True
        assert SchemaCatalog.from_rows([("t", ["c"])]).is_empty is False

    def test_all_columns_merges_across_tables(self):
        catalog = SchemaCatalog.from_rows([("a", ["x", "y"]), ("b", ["y", "z"])])
        assert catalog.all_columns() == {"x", "y", "z"}


class TestValidateIdentifiers:
    def _catalog(self) -> SchemaCatalog:
        return SchemaCatalog.from_rows([
            ("dbo.Attendance", ["UserId", "Status", "Date"]),
            ("dbo.Users", ["UserId", "UserName"]),
        ])

    def test_empty_catalog_skips_validation(self):
        validate_identifiers("SELECT hallucinated_col FROM NoSuchTable", SchemaCatalog())

    def test_known_table_and_columns_pass(self):
        validate_identifiers("SELECT UserId, Status FROM Attendance", self._catalog())

    def test_unknown_table_rejected(self):
        with pytest.raises(UnknownIdentifierError, match="unknown table"):
            validate_identifiers("SELECT * FROM Grades", self._catalog())

    def test_unknown_unqualified_column_rejected(self):
        with pytest.raises(UnknownIdentifierError, match="unknown column"):
            validate_identifiers("SELECT gpa_unweighted FROM Attendance", self._catalog())

    def test_unknown_qualified_column_rejected(self):
        with pytest.raises(UnknownIdentifierError, match="unknown column"):
            validate_identifiers("SELECT a.gpa FROM Attendance a", self._catalog())

    def test_join_across_known_tables_passes(self):
        validate_identifiers(
            "SELECT a.UserId, u.UserName FROM Attendance a JOIN Users u ON a.UserId = u.UserId",
            self._catalog(),
        )

    def test_aliased_expression_column_passes(self):
        validate_identifiers("SELECT COUNT(*) AS total FROM Attendance", self._catalog())

    def test_cte_output_columns_pass(self):
        validate_identifiers(
            "WITH t AS (SELECT UserId FROM Attendance) SELECT UserId FROM t",
            self._catalog(),
        )

    def test_unparseable_sql_raises(self):
        with pytest.raises(UnknownIdentifierError, match="could not parse"):
            validate_identifiers("SELECT FROM FROM FROM", self._catalog())


class TestSchemaCatalogCaching:
    def test_schema_catalog_is_cached(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_TABLE", "Attendance")
        store = SQLDataStore()
        calls = {"n": 0}

        def fake_schema_columns(database):
            calls["n"] += 1
            return pd.DataFrame({
                "TABLE_SCHEMA": ["dbo"],
                "TABLE_NAME": ["Attendance"],
                "COLUMN_NAME": ["UserId"],
            })

        monkeypatch.setattr(store, "schema_columns", fake_schema_columns)
        store._schema_catalog("db1")
        store._schema_catalog("db1")
        assert calls["n"] == 1

    def test_schema_catalog_degrades_to_empty_on_failure(self, monkeypatch):
        monkeypatch.setenv("PRIMARY_TABLE", "Attendance")
        store = SQLDataStore()
        monkeypatch.setattr(
            store, "schema_columns", lambda database: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        catalog = store._schema_catalog("db1")
        assert catalog.is_empty is True


class TestQueryWiresIdentifierGuard:
    def test_query_rejects_unknown_identifier_by_default(self, monkeypatch):
        monkeypatch.delenv("SQL_IDENTIFIER_GUARD", raising=False)
        monkeypatch.setenv("PRIMARY_TABLE", "Attendance")
        store = SQLDataStore()
        monkeypatch.setattr(
            store, "_schema_catalog",
            lambda database: SchemaCatalog.from_rows([("Attendance", ["UserId"])]),
        )
        with pytest.raises(UnknownIdentifierError):
            store._query("SELECT hallucinated_col FROM Attendance")

    def test_query_skips_identifier_guard_when_disabled(self, monkeypatch):
        monkeypatch.setenv("SQL_IDENTIFIER_GUARD", "false")
        monkeypatch.setenv("PRIMARY_TABLE", "Attendance")
        store = SQLDataStore()
        monkeypatch.setattr(
            store, "_schema_catalog",
            lambda database: SchemaCatalog.from_rows([("Attendance", ["UserId"])]),
        )
        monkeypatch.setattr(store, "_exec", lambda *a, **k: pd.DataFrame())
        store._query("SELECT hallucinated_col FROM Attendance")
