from __future__ import annotations

import json
import re

import pandas as pd
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool


_VALID_PERIODS = frozenset({"all", "last_7_days", "last_30_days"})
_VALID_VIEWS   = frozenset({"overview", "group", "entity"})


def _df_to_text(df: pd.DataFrame, max_rows: int = 50) -> str:
    """Return a compact analytical summary for the LLM.

    The full dataset is rendered as an interactive table for the user via the
    artifact — the LLM only needs enough context to write analysis, not to
    reproduce the data.
    """
    if df.empty:
        return "No data available."

    total = len(df)
    numeric_cols = df.select_dtypes(include="number").columns.tolist()

    # Aggregate stats for each numeric column
    stats: dict = {}
    for col in numeric_cols:
        s = df[col]
        stats[col] = {
            "min": round(float(s.min()), 2),
            "max": round(float(s.max()), 2),
            "mean": round(float(s.mean()), 2),
        }

    # Sort by the last numeric column (usually metric_rate or delta) to surface
    # top and bottom performers for the LLM to cite specific examples
    sort_col = numeric_cols[-1] if numeric_cols else None
    sorted_df = df.sort_values(sort_col, ascending=False) if sort_col else df
    top = sorted_df.head(5).to_dict(orient="records")
    bottom = sorted_df.tail(5).to_dict(orient="records")

    return json.dumps({
        "total_rows": total,
        "columns": df.columns.tolist(),
        "stats": stats,
        "top_5": top,
        "bottom_5": bottom,
        "instruction": (
            "The full table is already displayed to the user. "
            "Write analysis only — do not reproduce or reformat this data."
        ),
    }, default=str)


def _df_to_artifact(df: pd.DataFrame, max_rows: int = 50) -> dict:
    chunk = json.loads(df.head(max_rows).to_json(orient="split"))
    return {"columns": chunk["columns"], "rows": chunk["data"],
            "truncated": len(df) > max_rows, "total_rows": len(df)}


def _store(config: RunnableConfig):
    return config["configurable"].get("store")


def _rag_store(config: RunnableConfig):
    return config["configurable"].get("rag_store")


@tool(response_format="content_and_artifact")
def query_data(
    group_by: str = "",
    period:   str = "all",
    config:   RunnableConfig = None,
) -> tuple[str, dict]:
    """
    Return metric statistics grouped by a configured dimension.
    group_by: dimension to group by — leave empty for the default group dimension,
              or specify 'week', 'month', 'day_of_week', or the entity column.
    period:   'all' | 'last_7_days' | 'last_30_days'
    """
    if period not in _VALID_PERIODS:
        return f"Invalid period '{period}': must be one of 'all', 'last_7_days', 'last_30_days'.", {}
    if len(group_by) > 100:
        return "Invalid group_by: must be 100 characters or fewer.", {}
    store = _store(config)
    if store is None:
        return "No data store connected.", {}

    df = store.compute_stats(group_by, period)
    return _df_to_text(df), _df_to_artifact(df)


@tool(response_format="content_and_artifact")
def get_threshold_alerts(
    threshold: float = 75.0,
    config: RunnableConfig = None,
) -> tuple[str, dict]:
    """
    Return entities whose metric rate is below the given threshold (default 75%).
    Includes entity ID, label (if available), group, and metric rate.
    """
    if not 0.0 <= threshold <= 100.0:
        return f"Invalid threshold {threshold}: must be between 0.0 and 100.0.", {}
    store = _store(config)
    if store is None:
        return "No data store connected.", {}

    df = store.get_threshold_alerts(threshold=threshold)
    if df.empty:
        return f"No entities below {threshold}% metric threshold.", {}
    return _df_to_text(df), _df_to_artifact(df)


@tool
def get_summary(config: RunnableConfig = None) -> str:
    """
    Return a high-level summary of all loaded data:
    total records, entity count, date range, overall metric rate, and dimensions.
    """
    store = _store(config)
    if store is None:
        return "No data store connected."

    return json.dumps(store.summary(), indent=2)


@tool
def update_dashboard_view(
    segments: list[str] | None = None,
    period: str = "all",
    view: str = "overview",
    config: RunnableConfig = None,
) -> str:
    """
    Update the live dashboard to show a specific view.

    segments: list of segment names to filter by (empty list = all)
    period:   'all' | 'last_7_days' | 'last_30_days'
    view:     'overview' | 'group' | 'entity'

    Examples:
      update_dashboard_view(segments=["segment_a"], view="group")
      update_dashboard_view(period="last_30_days")
    """
    safe_period = period if period in _VALID_PERIODS else "all"
    safe_view   = view   if view   in _VALID_VIEWS   else "overview"
    return json.dumps({"segments": segments or [], "period": safe_period, "view": safe_view})


@tool(response_format="content_and_artifact")
def run_sql_query(
    sql: str,
    database: str = "",
    config: RunnableConfig = None,
) -> tuple[str, dict]:
    """
    Execute an ad-hoc T-SQL SELECT query against a named database.
    Only SELECT statements are permitted — DML and DDL are blocked.
    Results are capped at 200 rows.

    database: one of the configured database names (leave blank for the primary database).
    sql:      a valid T-SQL SELECT statement using SQL Server syntax.

    Use this when the built-in tools cannot answer a specific question.
    Always call retrieve_schema first to confirm table and column names.
    """
    if not sql.strip():
        return "SQL query cannot be empty.", {}
    if len(sql) > 5000:
        return f"SQL query too long ({len(sql)} chars): maximum is 5000.", {}
    store = _store(config)
    if store is None:
        return "No data store connected.", {}

    sql_clean = sql.strip().rstrip(";")
    if not re.search(r"\bTOP\s+\d+\b", sql_clean, re.IGNORECASE):
        sql_clean = re.sub(r"(?i)^\s*SELECT\b", "SELECT TOP 200", sql_clean, count=1)

    try:
        df = store._query(sql_clean, database=database or None)
    except PermissionError as e:
        return f"Query blocked: {e}", {}
    except Exception as e:
        return f"Query failed: {e}", {}

    if df.empty:
        return "Query returned no rows.", {}
    return _df_to_text(df, max_rows=200), _df_to_artifact(df, max_rows=200)


@tool(response_format="content_and_artifact")
def compare_periods(
    period_a: str = "last_7_days",
    period_b: str = "last_30_days",
    config: RunnableConfig = None,
) -> tuple[str, dict]:
    """
    Compare metric rates between two time periods across all groups.
    Returns a side-by-side table with a delta column.
    period_a, period_b: 'all' | 'last_7_days' | 'last_30_days'
    Example: compare_periods(period_a='last_7_days', period_b='last_30_days')
    """
    for name, val in [("period_a", period_a), ("period_b", period_b)]:
        if val not in _VALID_PERIODS:
            return f"Invalid {name} '{val}': must be one of 'all', 'last_7_days', 'last_30_days'.", {}
    store = _store(config)
    if store is None:
        return "No data store connected.", {}

    df_a = store.compute_stats("", period_a)
    df_b = store.compute_stats("", period_b)

    if df_a.empty and df_b.empty:
        return "No data available for either period.", {}

    dim = df_a.columns[0] if not df_a.empty else df_b.columns[0]
    df_a = df_a[[dim, "metric_rate"]].rename(columns={"metric_rate": f"rate_{period_a}"})
    df_b = df_b[[dim, "metric_rate"]].rename(columns={"metric_rate": f"rate_{period_b}"})

    merged = df_a.merge(df_b, on=dim, how="outer").fillna(0)
    delta  = (merged[f"rate_{period_b}"] - merged[f"rate_{period_a}"]).round(1)
    merged["delta"] = delta.apply(lambda d: f"+{d}" if d > 0 else str(d))

    return _df_to_text(merged), _df_to_artifact(merged)


@tool(response_format="content_and_artifact")
def compare_segments(
    segment_a: str,
    segment_b: str,
    config: RunnableConfig = None,
) -> tuple[str, dict]:
    """
    Compare metric statistics for two specific segments side by side.
    Returns total, positive_count, and metric_rate for each segment.
    Example: compare_segments(segment_a='segment_a', segment_b='segment_b')
    """
    if not segment_a or len(segment_a) > 100:
        return "Invalid segment_a: must be a non-empty string of at most 100 characters.", {}
    if not segment_b or len(segment_b) > 100:
        return "Invalid segment_b: must be a non-empty string of at most 100 characters.", {}
    store = _store(config)
    if store is None:
        return "No data store connected.", {}

    df = store.compute_stats("", "all", segments=[segment_a, segment_b])
    if df.empty:
        return f"No data found for segments '{segment_a}' or '{segment_b}'.", {}
    return _df_to_text(df), _df_to_artifact(df)


@tool
def retrieve_schema(
    query: str,
    config: RunnableConfig = None,
) -> str:
    """
    Look up database table and column definitions from the schema knowledge base.

    Call this BEFORE run_sql_query when unsure of a table name, column name,
    or data type — especially for non-primary databases.

    query: natural-language description of what tables or columns you need.
    Examples:
      'primary table columns'
      'secondary_db table schema'
      'what columns store entity group information'
    """
    if not query.strip():
        return "Schema query cannot be empty."
    if len(query) > 500:
        return f"Schema query too long ({len(query)} chars): maximum is 500."
    rag = _rag_store(config)
    if rag is None:
        return "Schema knowledge base not available."
    return rag.retrieve_schema(query)


@tool
def retrieve_policy(
    query: str,
    config: RunnableConfig = None,
) -> str:
    """
    Search policy and rule documents loaded into the knowledge base.

    Call this when the user asks about rules, thresholds, consequences,
    exemptions, or procedures — any question whose answer is in a policy
    document rather than in live data.

    query: natural-language description of the policy topic you need.
    Examples:
      'threshold for low performers'
      'exemption documentation requirements'
      'consequences for entities below threshold'
    """
    if not query.strip():
        return "Policy query cannot be empty."
    if len(query) > 500:
        return f"Policy query too long ({len(query)} chars): maximum is 500."
    rag = _rag_store(config)
    if rag is None:
        return "Policy knowledge base not available."
    return rag.retrieve_policy(query)


@tool
def statistical_summary(
    group_by: str = "",
    config:   RunnableConfig = None,
) -> str:
    """
    Return distribution statistics (count, mean, std, min, 25%, median, 75%, max)
    of metric_rate across all groups. Useful for understanding spread and outliers.
    group_by: dimension to group by (leave empty for the default group dimension).
    """
    if len(group_by) > 100:
        return "Invalid group_by: must be 100 characters or fewer."
    store = _store(config)
    if store is None:
        return "No data store connected."
    result = store.compute_statistical_summary(group_by)
    return json.dumps(result, indent=2)


@tool(response_format="content_and_artifact")
def detect_anomalies(
    group_by: str   = "",
    sigma:    float = 2.0,
    config:   RunnableConfig = None,
) -> tuple[str, dict]:
    """
    Identify groups/entities whose metric_rate deviates more than sigma standard
    deviations from the mean. Returns the anomalous rows with their z-scores.
    sigma: sensitivity threshold (default 2.0 — flags groups 2σ from the mean).
    """
    if not 0.1 <= sigma <= 10.0:
        return f"Invalid sigma {sigma}: must be between 0.1 and 10.0.", {}
    store = _store(config)
    if store is None:
        return "No data store connected.", {}
    df = store.detect_anomalies(group_by, sigma)
    if df.empty:
        return f"No anomalies detected beyond {sigma}σ from the mean.", {}
    return _df_to_text(df), _df_to_artifact(df)


@tool(response_format="content_and_artifact")
def get_top_n(
    group_by:  str  = "",
    n:         int  = 10,
    ascending: bool = True,
    config:    RunnableConfig = None,
) -> tuple[str, dict]:
    """
    Return the top or bottom N groups ranked by metric_rate.
    ascending=True  → lowest metric_rate first (worst performers).
    ascending=False → highest metric_rate first (best performers).
    """
    if not 1 <= n <= 50:
        return f"Invalid n {n}: must be between 1 and 50.", {}
    store = _store(config)
    if store is None:
        return "No data store connected.", {}
    df = store.get_top_n(group_by, n, ascending)
    if df.empty:
        return "No data available.", {}
    return _df_to_text(df), _df_to_artifact(df)


@tool
def analyze_trend(
    config: RunnableConfig = None,
) -> str:
    """
    Analyze the week-over-week trend of metric_rate.
    Returns direction (improving / declining / stable), slope per week,
    and the weekly breakdown table.
    """
    store = _store(config)
    if store is None:
        return "No data store connected."
    result = store.analyze_weekly_trend()
    return json.dumps(result, indent=2)


ALL_TOOLS = [
    query_data,
    get_threshold_alerts,
    get_summary,
    update_dashboard_view,
    run_sql_query,
    compare_periods,
    compare_segments,
    retrieve_schema,
    retrieve_policy,
    statistical_summary,
    detect_anomalies,
    get_top_n,
    analyze_trend,
]
