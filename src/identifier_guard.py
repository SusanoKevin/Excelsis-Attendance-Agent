from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import expressions as exp


class UnknownIdentifierError(ValueError):
    """Raised when a statement references a table or column not in the schema."""


@dataclass
class SchemaCatalog:
    """Case-insensitive lookup of the tables and columns a database exposes.

    The SQL guard (`_assert_select_only`) proves a statement is *safe*; it
    cannot prove the statement refers to columns that exist. A model that
    invents `students.gpa_unweighted` produces valid, harmless SQL that fails
    at the database with a confusing error — or worse, on a table with a
    similarly named column, silently returns the wrong data for a FERPA-
    covered student record. This catalog lets `validate_identifiers` reject
    that query before it ever reaches SQL Server.
    """

    # lowercase bare table name -> set of lowercase column names
    tables: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: list[tuple[str, list[str]]]) -> "SchemaCatalog":
        catalog = cls()
        for table_name, columns in rows:
            bare = table_name.split(".")[-1].strip("[]").lower()
            catalog.tables.setdefault(bare, set()).update(
                c.strip("[]").lower() for c in columns
            )
        return catalog

    @property
    def is_empty(self) -> bool:
        return not self.tables

    def all_columns(self) -> set[str]:
        merged: set[str] = set()
        for cols in self.tables.values():
            merged |= cols
        return merged


def _bare(name: str) -> str:
    return name.split(".")[-1].strip("[]").lower()


def validate_identifiers(sql: str, catalog: SchemaCatalog, dialect: str = "tsql") -> None:
    """Raise UnknownIdentifierError if `sql` references anything unknown.

    Skips validation entirely when the catalog is empty (schema introspection
    unavailable or not yet run) — there is nothing to validate against, and
    `_assert_select_only` plus the read-only DB role still apply.
    """
    if catalog.is_empty:
        return

    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception as e:  # noqa: BLE001
        raise UnknownIdentifierError(f"could not parse generated SQL: {e}") from e

    # Aliases introduced by the query itself (CTEs, subquery aliases) are valid
    # table references even though they are not in the catalog.
    local_names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        if cte.alias:
            local_names.add(cte.alias.lower())

    referenced_tables: set[str] = set()
    alias_to_table: dict[str, str] = {}

    for table in tree.find_all(exp.Table):
        name = _bare(table.name)
        if not name:
            continue
        alias = (table.alias or "").lower()
        if name not in local_names:
            referenced_tables.add(name)
            if alias:
                alias_to_table[alias] = name
        elif alias:
            local_names.add(alias)

    unknown_tables = sorted(t for t in referenced_tables if t not in catalog.tables)
    if unknown_tables:
        raise UnknownIdentifierError(f"unknown table(s): {', '.join(unknown_tables)}")

    # Names the query itself introduces — `SUM(total) AS spend`, a CTE's output
    # columns, a derived table's projection. These are legitimate references
    # even though no base table declares them. Only an explicit `AS` introduces
    # a new name — a bare `SELECT col` is a *reference* and must still be
    # validated, or a hallucinated column would whitelist itself.
    derived_names: set[str] = set()
    for alias in tree.find_all(exp.Alias):
        if alias.alias:
            derived_names.add(alias.alias.lower())
    for cte in tree.find_all(exp.CTE):
        for col in cte.args.get("alias", {}).args.get("columns", []) or []:
            derived_names.add(col.name.lower())

    # Column validation. Where a column is qualified we check it against that
    # specific table; unqualified columns are checked against the union of the
    # referenced tables, which avoids false positives from natural joins.
    if referenced_tables:
        candidate_columns: set[str] = set()
        for t in referenced_tables:
            candidate_columns |= catalog.tables.get(t, set())
    else:
        # Only CTEs/derived tables are read from; there is no base table to
        # validate against, so accept anything the query defined itself.
        candidate_columns = catalog.all_columns()
    candidate_columns |= derived_names

    unknown_columns: set[str] = set()
    for column in tree.find_all(exp.Column):
        col = _bare(column.name)
        if not col or col == "*":
            continue

        qualifier = (column.table or "").lower()
        if qualifier:
            if qualifier in local_names:
                continue  # column of a CTE/derived table
            target = alias_to_table.get(qualifier, qualifier)
            known = catalog.tables.get(target)
            if known is None:
                continue  # unresolvable qualifier; table check already passed
            if col not in known:
                unknown_columns.add(f"{qualifier}.{col}")
        elif col not in candidate_columns:
            unknown_columns.add(col)

    if unknown_columns:
        raise UnknownIdentifierError(f"unknown column(s): {', '.join(sorted(unknown_columns))}")
