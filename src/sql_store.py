from __future__ import annotations

import contextlib
import os
import re
import threading
import time
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import sqlglot
from prometheus_client import Counter
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool
from sqlglot import exp

from .identifier_guard import SchemaCatalog, validate_identifiers

_cache_hits: Counter = Counter(
    "cache_hits_total",
    "Cache hits by cache name",
    ["cache"],
)
_cache_misses: Counter = Counter(
    "cache_misses_total",
    "Cache misses by cache name",
    ["cache"],
)

_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
    exp.Alter, exp.TruncateTable, exp.Merge, exp.Command,
)

_BLOCKED_SCHEMAS = frozenset({"information_schema", "sys", "sysobjects"})

_BLOCKED_COLUMNS = frozenset({"password", "hashed_password", "ssn"})

# T-SQL constructs that are dangerous even inside a syntactically valid SELECT
# (extended stored procedures, linked-server/file access, timing attacks).
# sqlglot may parse these as ordinary function calls rather than a distinct
# node type, so a regex over the raw text is defence-in-depth alongside the
# AST walk below, not a replacement for it.
_BLOCKLIST = re.compile(
    r"\b("
    r"xp_cmdshell|xp_dirtree|xp_fileexist|xp_regread|xp_regwrite|"
    r"sp_oacreate|sp_oamethod|sp_execute|sp_executesql|"
    r"openrowset|opendatasource|openquery|openxml|"
    r"bulk\s+insert|waitfor\s+delay|waitfor\s+time"
    r")\b",
    re.IGNORECASE,
)


def _assert_select_only(sql: str) -> None:
    if _BLOCKLIST.search(sql):
        raise PermissionError("Read-only store: statement contains a blocked function or construct.")
    try:
        trees = [t for t in sqlglot.parse(sql.replace("?", "NULL"), dialect="tsql") if t is not None]
    except Exception as e:
        raise ValueError(f"Could not parse SQL: {e}") from e
    if not trees:
        raise ValueError("Empty SQL statement.")
    if len(trees) > 1:
        raise PermissionError("Read-only store: only a single SELECT statement is permitted.")
    if not isinstance(trees[0], exp.Select):
        raise PermissionError("Read-only store: only SELECT statements are permitted.")
    for node in trees[0].walk():
        if isinstance(node, _FORBIDDEN):
            raise PermissionError(f"Read-only store: {type(node).__name__} statements are not permitted.")
        if isinstance(node, exp.Select) and node.args.get("into"):
            raise PermissionError("Read-only store: SELECT INTO is not permitted.")
        if isinstance(node, exp.Table):
            db_part = (node.args.get("db") or node.args.get("catalog") or exp.Identifier(this="")).name
            tbl_part = node.name
            if db_part.lower() in _BLOCKED_SCHEMAS or tbl_part.lower() in _BLOCKED_SCHEMAS:
                raise PermissionError(
                    f"Read-only store: access to '{db_part or tbl_part}' is not permitted."
                )
        if isinstance(node, exp.Column) and node.name.lower() in _BLOCKED_COLUMNS:
            raise PermissionError(
                f"Read-only store: column '{node.name}' is not permitted. "
                "Select other columns, or use SELECT * if you need the full row."
            )


def _set_lock_timeout(dbapi_conn, lock_timeout_ms: int) -> None:
    cur = dbapi_conn.cursor()
    try:
        cur.execute(f"SET LOCK_TIMEOUT {lock_timeout_ms}")
        dbapi_conn.commit()
    finally:
        cur.close()


def _apply_lock_timeout(engine: Engine) -> None:
    """Bound how long a query waits on a lock before failing.

    SQL Server has no session-level read-only mode — that guarantee has to
    come from the login/role granted in the DSN (grant only db_datareader).
    LOCK_TIMEOUT is a second, independent guard this app *can* enforce from
    the connection itself: even a misconfigured or overly-privileged login
    can't hang the connection pool waiting on another session's lock.
    """
    lock_timeout_ms = int(os.environ.get("SQL_LOCK_TIMEOUT_MS", "5000"))

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # noqa: ANN001
        _set_lock_timeout(dbapi_conn, lock_timeout_ms)


def _build_engine(database: str) -> Engine:
    server      = os.environ["SQL_SERVER"]
    driver      = os.environ.get("SQL_DRIVER", "{ODBC Driver 18 for SQL Server}")
    pool_size   = int(os.environ.get("SQL_POOL_SIZE", "5"))
    timeout     = int(os.environ.get("SQL_QUERY_TIMEOUT", "30"))
    auth_method = os.environ.get("SQL_AUTH_METHOD", "sql")
    if auth_method == "windows":
        dsn = (
            f"DRIVER={driver};SERVER={server};DATABASE={database};"
            f"Trusted_Connection=yes;TrustServerCertificate=yes;"
        )
    else:
        username = os.environ["SQL_USERNAME"]
        password = os.environ["SQL_PASSWORD"]
        dsn = (
            f"DRIVER={driver};SERVER={server};DATABASE={database};"
            f"UID={username};PWD={password};TrustServerCertificate=yes;"
        )
    url = f"mssql+pyodbc:///?odbc_connect={quote_plus(dsn)}"
    engine = create_engine(
        url,
        poolclass=QueuePool,
        pool_size=pool_size,
        max_overflow=10,
        pool_pre_ping=True,
        connect_args={"timeout": timeout},
    )
    _apply_lock_timeout(engine)
    return engine


class _TTLCache:
    def __init__(self, ttl: int = 300, maxsize: int = 0, name: str = "sql") -> None:
        self._store: dict = {}
        self._ttl = ttl
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._name = name

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                _cache_misses.labels(cache=self._name).inc()
                return None
            value, expires = entry
            if time.monotonic() > expires:
                self._store.pop(key, None)
                _cache_misses.labels(cache=self._name).inc()
                return None
            _cache_hits.labels(cache=self._name).inc()
            return value

    def set(self, key: str, value) -> None:
        with self._lock:
            if self._maxsize > 0 and len(self._store) >= self._maxsize and key not in self._store:
                oldest = min(self._store, key=lambda k: self._store[k][1])
                del self._store[oldest]
            self._store[key] = (value, time.monotonic() + self._ttl)


def _ph(n: int) -> str:
    return ",".join(["?"] * n)


class SQLDataStore:
    def __init__(self) -> None:
        self._primary_db = os.environ.get("SQL_PRIMARY_DB", "")
        self._databases: list[str] = [
            d.strip()
            for d in os.environ.get("SQL_DATABASES", self._primary_db).split(",")
            if d.strip()
        ]
        self._table           = os.environ.get("PRIMARY_TABLE",       "")
        self._metric_col      = os.environ.get("METRIC_COLUMN",       "status")
        self._positive_val    = os.environ.get("POSITIVE_VALUE",      "active")
        self._date_col        = os.environ.get("DATE_COLUMN",         "date")
        self._entity_col      = os.environ.get("ENTITY_COLUMN",       "entity_id")
        self._entity_name_col = os.environ.get("ENTITY_NAME_COLUMN",  "entity_name")
        self._group_cols: list[str] = [
            c.strip()
            for c in os.environ.get("GROUP_COLUMNS", "").split(",")
            if c.strip()
        ]
        self._group_expr = self._build_group_expr()
        self._engines: dict[str, Engine] = {db: _build_engine(db) for db in self._databases}
        _sql_ttl     = int(os.environ.get("SQL_CACHE_TTL", "300"))
        _sql_maxsize = int(os.environ.get("SQL_CACHE_MAX_SIZE", "512"))
        self._cache  = _TTLCache(ttl=_sql_ttl, maxsize=_sql_maxsize)
        _catalog_ttl = int(os.environ.get("SQL_IDENTIFIER_CATALOG_TTL", "3600"))
        self._catalog_cache = _TTLCache(ttl=_catalog_ttl, maxsize=len(self._databases) or 1, name="identifier_catalog")

    @staticmethod
    def _q(name: str) -> str:
        """Bracket-quote a SQL Server identifier. Dotted names (e.g. an alias-
        qualified column like 'a.UserId', needed when PRIMARY_TABLE is a join)
        are quoted part-by-part rather than as one bracketed blob."""
        return ".".join(f"[{part.replace(']', ']]')}]" for part in name.split("."))

    def _table_expr(self) -> str:
        """FROM-clause source for structured queries. A bare table name is
        bracket-quoted as before; a value containing whitespace is treated as
        a full FROM-clause expression (e.g. a JOIN across multiple tables) and
        used as-is, so PRIMARY_TABLE isn't limited to a single physical table."""
        table = self._require_table()
        if table.strip() and any(c.isspace() for c in table):
            if ";" in table:
                raise RuntimeError(
                    "PRIMARY_TABLE must not contain a semicolon. Configure it as a "
                    "plain table name (e.g. 'Attendance') or a single FROM-clause "
                    "expression (e.g. 'Attendance a JOIN Users u ON a.UserId=u.UserId')."
                )
            return table
        return self._q(table)

    def _exec_primary(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        """Run a structured-tool query against PRIMARY_TABLE. Translates any
        failure — a misconfigured table/join, a wrong column name, etc. — into
        a message pointing at the schema-driven fallback, since these queries
        are built from admin config that may not match the real schema."""
        try:
            return self._exec(sql, params)
        except PermissionError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"Structured query against PRIMARY_TABLE ('{self._table}') failed: {e}. "
                "Use retrieve_schema to inspect the real schema, then run_sql_query "
                "to answer this directly instead."
            ) from e

    def _build_period_clause(
        self, period: str, date_from: str | None, date_to: str | None, params: list
    ) -> str:
        dc = self._q(self._date_col)
        t  = self._table_expr()
        if date_from or date_to:
            parts: list[str] = []
            if date_from: parts.append(f"AND {dc} >= ?"); params.append(date_from)
            if date_to:   parts.append(f"AND {dc} <= ?"); params.append(date_to)
            return " ".join(parts)
        if period == "last_7_days":
            return f"AND {dc} >= (SELECT DATEADD(dd,-7,MAX({dc})) FROM {t})"
        if period == "last_30_days":
            return f"AND {dc} >= (SELECT DATEADD(dd,-30,MAX({dc})) FROM {t})"
        if period == "prior_7_days":
            return (f"AND {dc} >= (SELECT DATEADD(dd,-14,MAX({dc})) FROM {t}) "
                    f"AND {dc} <  (SELECT DATEADD(dd,-7,MAX({dc})) FROM {t})")
        if period == "prior_30_days":
            return (f"AND {dc} >= (SELECT DATEADD(dd,-60,MAX({dc})) FROM {t}) "
                    f"AND {dc} <  (SELECT DATEADD(dd,-30,MAX({dc})) FROM {t})")
        return ""

    def _build_group_expr(self) -> dict[str, tuple[str, str]]:
        dc  = self._q(self._date_col)
        ec  = self._q(self._entity_col)
        expr: dict[str, tuple[str, str]] = {}
        for col in self._group_cols:
            qcol = self._q(col)
            expr[col] = (qcol, col)
        expr[self._entity_col] = (f"CAST({ec} AS NVARCHAR(50))", self._entity_col)
        expr["week"] = (
            f"CONVERT(NVARCHAR(10),DATEADD(dd,1-DATEPART(dw,{dc}),{dc}),23)"
            f"+'/'+"
            f"CONVERT(NVARCHAR(10),DATEADD(dd,7-DATEPART(dw,{dc}),{dc}),23)",
            "week",
        )
        expr["month"]       = (f"FORMAT({dc},'yyyy-MM')", "month")
        expr["day_of_week"] = (f"DATENAME(dw,{dc})",     "day_of_week")
        return expr

    @property
    def databases(self) -> list[str]:
        return list(self._databases)

    @property
    def primary_db(self) -> str:
        return self._primary_db

    def _require_table(self) -> str:
        if not self._table:
            raise RuntimeError(
                "PRIMARY_TABLE is not configured. Set it in your .env file to a table "
                "name or a FROM-clause join expression, or use retrieve_schema and "
                "run_sql_query to answer this directly instead."
            )
        return self._table

    def _exec(self, sql: str, params: tuple = (), database: str | None = None) -> pd.DataFrame:
        """Trusted internal executor — no parse-time guard. Use _query for LLM-provided SQL."""
        target_db = database or self._primary_db
        if target_db not in self._databases:
            raise PermissionError(f"Database '{target_db}' is not in the configured allowlist.")
        with contextlib.closing(self._engines[target_db].raw_connection()) as conn:
            return pd.read_sql(sql, conn, params=params or None)

    def _schema_catalog(self, database: str) -> SchemaCatalog:
        """Cached table/column catalog for `validate_identifiers`. Failing to
        build it (e.g. a permissions issue reading INFORMATION_SCHEMA) must
        not break query execution — an empty catalog just skips identifier
        validation, leaving `_assert_select_only` and the DB role as the
        remaining guards."""
        cache_key = f"catalog:{database}"
        cached = self._catalog_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            cols_df = self.schema_columns(database)
            grouped: dict[str, list[str]] = {}
            for _, row in cols_df.iterrows():
                table = f"{row['TABLE_SCHEMA']}.{row['TABLE_NAME']}"
                grouped.setdefault(table, []).append(row["COLUMN_NAME"])
            catalog = SchemaCatalog.from_rows(list(grouped.items()))
        except Exception:
            catalog = SchemaCatalog()
        self._catalog_cache.set(cache_key, catalog)
        return catalog

    def _query(self, sql: str, params: tuple = (), database: str | None = None) -> pd.DataFrame:
        _assert_select_only(sql)
        if os.environ.get("SQL_IDENTIFIER_GUARD", "true").lower() != "false":
            target_db = database or self._primary_db
            validate_identifiers(sql.replace("?", "NULL"), self._schema_catalog(target_db))
        return self._exec(sql, params, database)

    def schema_columns(self, database: str) -> pd.DataFrame:
        """Return INFORMATION_SCHEMA.COLUMNS for internal schema introspection only."""
        return self._exec(
            "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME,"
            " DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE"
            " FROM INFORMATION_SCHEMA.COLUMNS"
            " ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION",
            database=database,
        )

    def ping(self, database: str | None = None) -> bool:
        try:
            self._exec("SELECT 1", database=database)
            return True
        except Exception:
            return False

    def close(self) -> None:
        for engine in self._engines.values():
            engine.dispose()

    def summary(self) -> dict:
        self._require_table()
        cached = self._cache.get("summary:all")
        if cached is not None:
            return cached

        t  = self._table_expr()
        mc = self._q(self._metric_col)
        dc = self._q(self._date_col)
        ec = self._q(self._entity_col)
        pv = self._positive_val
        df = self._exec_primary(f"""
        SELECT
            COUNT(*)                                                          AS total_records,
            COUNT(DISTINCT {ec})                                              AS entity_count,
            CONVERT(NVARCHAR(10),MIN({dc}),23)                               AS date_from,
            CONVERT(NVARCHAR(10),MAX({dc}),23)                               AS date_to,
            ROUND(100.0*SUM(CASE WHEN {mc}=? THEN 1 ELSE 0 END)
                /NULLIF(COUNT(*),0),1)                                        AS metric_rate,
            SUM(CASE WHEN {mc}<>? THEN 1 ELSE 0 END)                        AS below_threshold_count
        FROM {t}
        """, params=(pv, pv))

        if df.empty or int(df["total_records"].iloc[0]) == 0:
            return {"status": "no_data"}

        first_dim = self._group_cols[0] if self._group_cols else None
        first_dim_q = self._q(first_dim) if first_dim else None
        dims_df = self._exec_primary(
            f"SELECT DISTINCT {first_dim_q} FROM {t} "
            f"WHERE {first_dim_q} IS NOT NULL ORDER BY {first_dim_q}"
        ) if first_dim else pd.DataFrame()

        row = df.iloc[0]
        result = {
            "total_records":         int(row["total_records"]),
            "entity_count":          int(row["entity_count"]),
            "date_range":            {"from": str(row["date_from"]), "to": str(row["date_to"])},
            "metric_rate":           float(row["metric_rate"]),
            "below_threshold_count": int(row["below_threshold_count"]),
            "dimensions":            dims_df.iloc[:, 0].tolist() if first_dim and not dims_df.empty else [],  # noqa: E501
        }
        self._cache.set("summary:all", result)
        return result

    def compute_stats(
        self,
        group_by:  str = "",
        period:    str = "all",
        segments:  list[str] | None = None,
        date_from: str | None = None,
        date_to:   str | None = None,
    ) -> pd.DataFrame:
        self._require_table()
        group_by  = group_by or (self._group_cols[0] if self._group_cols else self._entity_col)
        cache_key = f"stats:{group_by}:{period}:{','.join(sorted(segments or []))}:{date_from or ''}:{date_to or ''}"
        cached    = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if group_by not in self._group_expr:
            matches = [k for k in self._group_expr if k.startswith(group_by)]
            if len(matches) == 1:
                group_by = matches[0]
            else:
                raise ValueError(f"Invalid group_by '{group_by}'. Valid: {', '.join(self._group_expr)}")
        col_expr, col_alias = self._group_expr[group_by]
        t  = self._table_expr()
        mc = self._q(self._metric_col)
        pv = self._positive_val
        params: list = [pv, pv]

        period_clause = self._build_period_clause(period, date_from, date_to, params)

        segment_clause = ""
        if segments and self._group_cols:
            grp0_q = self._q(self._group_cols[0])
            segment_clause = f"AND {grp0_q} IN ({_ph(len(segments))})"
            params.extend(segments)

        result = self._exec_primary(f"""
        SELECT
            {col_expr}  AS [{col_alias}],
            COUNT(*)                                                          AS total,
            SUM(CASE WHEN {mc}=? THEN 1 ELSE 0 END)                         AS positive_count,
            ROUND(100.0*SUM(CASE WHEN {mc}=? THEN 1 ELSE 0 END)
                /NULLIF(COUNT(*),0),1)                                        AS metric_rate
        FROM {t}
        WHERE {col_expr} IS NOT NULL
        {period_clause}
        {segment_clause}
        GROUP BY {col_expr}
        """, params=tuple(params))
        if col_alias not in ("week", "month", "day_of_week", "class"):
            result = result.rename(columns={col_alias: "class"})
        self._cache.set(cache_key, result)
        return result

    def get_threshold_alerts(
        self,
        threshold: float = 75.0,
        segments:  list[str] | None = None,
        date_from: str | None = None,
        date_to:   str | None = None,
    ) -> pd.DataFrame:
        self._require_table()
        cache_key = f"alerts:{threshold}:{','.join(sorted(segments or []))}:{date_from or ''}:{date_to or ''}"
        cached    = self._cache.get(cache_key)
        if cached is not None:
            return cached

        t   = self._table_expr()
        mc  = self._q(self._metric_col)
        ec  = self._q(self._entity_col)
        enc = self._q(self._entity_name_col)
        pv  = self._positive_val
        grp0     = self._group_cols[0] if self._group_cols else None
        grp0_q   = self._q(grp0) if grp0 else None
        params: list  = [pv, pv]

        segment_clause = ""
        if segments and grp0_q:
            segment_clause = f"AND {grp0_q} IN ({_ph(len(segments))})"
            params.extend(segments)

        date_clause = self._build_period_clause("", date_from, date_to, params)
        params.append(pv)
        params.append(threshold)

        result = self._exec_primary(f"""
        SELECT
            {ec}                                                              AS entity_id,
            MAX({enc})                                                        AS label,
            {f"MAX({grp0_q})" if grp0_q else "NULL"}                         AS group_name,
            COUNT(*)                                                          AS total,
            SUM(CASE WHEN {mc}=? THEN 1 ELSE 0 END)                         AS positive_count,
            ROUND(100.0*SUM(CASE WHEN {mc}=? THEN 1 ELSE 0 END)
                /NULLIF(COUNT(*),0),1)                                        AS metric_rate
        FROM {t}
        WHERE 1=1
        {segment_clause}
        {date_clause}
        GROUP BY {ec}
        HAVING ROUND(100.0*SUM(CASE WHEN {mc}=? THEN 1 ELSE 0 END)
            /NULLIF(COUNT(*),0),1) < ?
        ORDER BY metric_rate ASC
        """, params=tuple(params))
        if "group_name" in result.columns:
            result["group_name"] = pd.Series(
                [None if pd.isna(v) else v for v in result["group_name"]],
                index=result.index, dtype=object,
            )
        self._cache.set(cache_key, result)
        return result

    def entity_weekly_rates(self, entity_ids: list, weeks: int = 6) -> dict:
        if not entity_ids:
            return {}
        self._require_table()
        cache_key = f"weekly:{','.join(str(e) for e in sorted(entity_ids))}:{weeks}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        t   = self._table_expr()
        mc  = self._q(self._metric_col)
        ec  = self._q(self._entity_col)
        pv  = self._positive_val
        week_expr = self._group_expr["week"][0]
        df = self._exec_primary(f"""
        SELECT
            {ec}                                        AS entity_id,
            {week_expr}                                 AS week,
            COUNT(*)                                    AS total,
            SUM(CASE WHEN {mc}=? THEN 1 ELSE 0 END)    AS positive_count
        FROM {t}
        WHERE {ec} IN ({_ph(len(entity_ids))})
        GROUP BY {ec}, {week_expr}
        ORDER BY week ASC
        """, params=(pv, *entity_ids))

        if df.empty:
            return {}

        all_weeks = sorted(df["week"].unique())[-weeks:]
        df        = df[df["week"].isin(all_weeks)].copy()
        df["rate"] = (df["positive_count"] / df["total"] * 100).round(1)
        pivot = (
            df.pivot(index="entity_id", columns="week", values="rate")
            .reindex(columns=all_weeks)
        )
        result = {
            eid: [None if pd.isna(v) else float(v) for v in pivot.loc[eid]]
            if eid in pivot.index else [None] * len(all_weeks)
            for eid in entity_ids
        }
        self._cache.set(cache_key, result)
        return result

    def compute_statistical_summary(
        self,
        group_by:  str = "",
        period:    str = "all",
        segments:  list[str] | None = None,
        date_from: str | None = None,
        date_to:   str | None = None,
    ) -> dict:
        df = self.compute_stats(group_by, period, segments, date_from, date_to)
        if df.empty or "metric_rate" not in df.columns:
            return {"error": "No data available."}
        return df["metric_rate"].describe().round(1).to_dict()

    def detect_anomalies(
        self,
        group_by:  str   = "",
        sigma:     float = 2.0,
        period:    str   = "all",
        segments:  list[str] | None = None,
        date_from: str | None = None,
        date_to:   str | None = None,
    ) -> pd.DataFrame:
        df = self.compute_stats(group_by, period, segments, date_from, date_to).copy()
        if df.empty or "metric_rate" not in df.columns:
            return pd.DataFrame()
        mean = df["metric_rate"].mean()
        std  = df["metric_rate"].std()
        if std == 0:
            return pd.DataFrame()
        df["z_score"] = ((df["metric_rate"] - mean) / std).round(2)
        return df[df["z_score"].abs() > sigma].sort_values("z_score").reset_index(drop=True)

    def get_top_n(
        self,
        group_by:    str  = "",
        n:           int  = 10,
        ascending:   bool = True,
        period:      str  = "all",
        segments:    list[str] | None = None,
        date_from:   str | None = None,
        date_to:     str | None = None,
        min_records: int = 1,
    ) -> pd.DataFrame:
        df = self.compute_stats(group_by, period, segments, date_from, date_to)
        if df.empty or "metric_rate" not in df.columns:
            return pd.DataFrame()
        if min_records > 1 and "total" in df.columns:
            df = df[df["total"] >= min_records]
            if df.empty:
                return pd.DataFrame()
        return (
            df.nsmallest(n, "metric_rate") if ascending
            else df.nlargest(n, "metric_rate")
        ).reset_index(drop=True)

    def analyze_weekly_trend(
        self,
        segments:  list[str] | None = None,
        date_from: str | None = None,
        date_to:   str | None = None,
    ) -> dict:
        cache_key = f"trend:{','.join(sorted(segments or []))}:{date_from or ''}:{date_to or ''}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        df = self.compute_stats("week", "all", segments, date_from, date_to)
        if df.empty or "metric_rate" not in df.columns or len(df) < 2:
            return {"direction": "unknown", "slope_per_week": 0.0, "weeks": []}
        df    = df.sort_values("week").reset_index(drop=True)
        rates = df["metric_rate"].values.astype(float)
        slope = float(np.polyfit(range(len(rates)), rates, 1)[0])
        if   slope >  0.1: direction = "improving"
        elif slope < -0.1: direction = "declining"
        else:              direction = "stable"
        result = {
            "direction":      direction,
            "slope_per_week": round(slope, 2),
            "weeks":          df.to_dict(orient="records"),
        }
        self._cache.set(cache_key, result)
        return result
