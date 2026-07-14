from __future__ import annotations

import time
import uuid
from datetime import timedelta

import numpy as np
import pandas as pd

VALID_STATUSES = {"active", "inactive", "partial", "exempt"}


class _TTLCache:
    def __init__(self, ttl: int = 300) -> None:
        self._store: dict = {}
        self._ttl = ttl

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires = entry
        if time.monotonic() > expires:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value) -> None:
        self._store[key] = (value, time.monotonic() + self._ttl)

    def clear(self) -> None:
        self._store.clear()


class SampleDataStore:
    """In-memory pandas-backed store used exclusively as a test fixture."""

    def __init__(self) -> None:
        self._datasets: dict[str, dict] = {}
        self._cache = _TTLCache(ttl=300)

    def ingest_df(self, df: pd.DataFrame, name: str = "uploaded") -> dict:
        did = str(uuid.uuid4())
        df = df.copy()
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
        df["date"]        = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
        df["status"]      = df["status"].str.strip().str.lower()
        df                = df[df["status"].isin(VALID_STATUSES)].copy()
        df["is_positive"] = df["status"] == "active"
        df["week"]        = df["date"].dt.to_period("W").astype(str)
        df["month"]       = df["date"].dt.to_period("M").astype(str)
        df["day_of_week"] = df["date"].dt.day_name()
        self._datasets[did] = {"name": name, "df": df}
        self._cache.clear()
        return {"dataset_id": did, "rows": len(df), "columns": list(df.columns)}

    def merged(self) -> pd.DataFrame:
        if not self._datasets:
            return pd.DataFrame()
        return pd.concat([v["df"] for v in self._datasets.values()], ignore_index=True)

    def get_threshold_alerts(
        self,
        threshold: float = 75.0,
        segments=None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> pd.DataFrame:
        cache_key = f"at_risk:{threshold}:{','.join(sorted(segments or []))}:{date_from or ''}:{date_to or ''}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        df = self.merged()
        if df.empty:
            return pd.DataFrame()
        if segments and "segment" in df.columns:
            df = df[df["segment"].isin(segments)]
        if date_from:
            df = df[df["date"] >= pd.to_datetime(date_from)]
        if date_to:
            df = df[df["date"] <= pd.to_datetime(date_to)]
        if df.empty:
            return pd.DataFrame()

        agg_cols: dict = {
            "total":    ("status",      "count"),
            "positive": ("is_positive", "sum"),
        }
        if "entity_name" in df.columns:
            agg_cols["name"] = ("entity_name", "first")
        if "segment" in df.columns:
            agg_cols["group_name"] = ("segment", "first")
        agg = df.groupby("entity_id").agg(**agg_cols).reset_index()
        agg["metric_rate"] = (agg["positive"] / agg["total"] * 100).round(1)
        result = agg[agg["metric_rate"] < threshold].sort_values("metric_rate")
        self._cache.set(cache_key, result)
        return result

    def compute_stats(
        self,
        group_by: str = "segment",
        period: str = "all",
        segments=None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> pd.DataFrame:
        cache_key = f"stats:{group_by}:{period}:{','.join(sorted(segments or []))}:{date_from or ''}:{date_to or ''}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        df = self.merged()
        if df.empty:
            return pd.DataFrame()
        if segments and "segment" in df.columns:
            df = df[df["segment"].isin(segments)]
            if df.empty:
                return pd.DataFrame()

        if date_from or date_to:
            if date_from:
                df = df[df["date"] >= pd.to_datetime(date_from)]
            if date_to:
                df = df[df["date"] <= pd.to_datetime(date_to)]
        else:
            ref = df["date"].max()
            if period == "last_7_days":
                df = df[df["date"] >= ref - timedelta(days=7)]
            elif period == "last_30_days":
                df = df[df["date"] >= ref - timedelta(days=30)]
            elif period == "prior_30_days":
                cutoff = ref - timedelta(days=30)
                df = df[(df["date"] < cutoff) & (df["date"] >= cutoff - timedelta(days=30))]

        if df.empty:
            return pd.DataFrame()

        col = group_by if group_by in df.columns else ("segment" if "segment" in df.columns else "entity_id")
        g = df.groupby(col).agg(
            total=("status",      "count"),
            positive=("is_positive", "sum"),
        ).reset_index()
        g["metric_rate"] = (g["positive"] / g["total"] * 100).round(1)
        self._cache.set(cache_key, g)
        return g

    def entity_weekly_rates(self, entity_ids: list, weeks: int = 6) -> dict:
        df = self.merged()
        if df.empty or not entity_ids:
            return {}
        df = df[df["entity_id"].isin(entity_ids)]
        if df.empty:
            return {}
        all_weeks = sorted(df["week"].unique())[-weeks:]
        df = df[df["week"].isin(all_weeks)]
        grp = (
            df.groupby(["entity_id", "week"])
            .agg(total=("status", "count"), positive=("is_positive", "sum"))
            .reset_index()
        )
        grp["rate"] = (grp["positive"] / grp["total"] * 100).round(1)
        pivot = grp.pivot(index="entity_id", columns="week", values="rate").reindex(columns=all_weeks)
        return {
            eid: [None if pd.isna(v) else float(v) for v in pivot.loc[eid]]
            if eid in pivot.index else [None] * len(all_weeks)
            for eid in entity_ids
        }

    def compute_statistical_summary(self, group_by: str = "") -> dict:
        df = self.compute_stats(group_by)
        if df.empty or "metric_rate" not in df.columns:
            return {"error": "No data available."}
        return df["metric_rate"].describe().round(1).to_dict()

    def detect_anomalies(self, group_by: str = "", sigma: float = 2.0) -> pd.DataFrame:
        df = self.compute_stats(group_by).copy()
        if df.empty or "metric_rate" not in df.columns:
            return pd.DataFrame()
        mean = df["metric_rate"].mean()
        std  = df["metric_rate"].std()
        if std == 0:
            return pd.DataFrame()
        df["z_score"] = ((df["metric_rate"] - mean) / std).round(2)
        return df[df["z_score"].abs() > sigma].sort_values("z_score").reset_index(drop=True)

    def get_top_n(self, group_by: str = "", n: int = 10, ascending: bool = True) -> pd.DataFrame:
        df = self.compute_stats(group_by)
        if df.empty or "metric_rate" not in df.columns:
            return pd.DataFrame()
        return (
            df.nsmallest(n, "metric_rate") if ascending
            else df.nlargest(n, "metric_rate")
        ).reset_index(drop=True)

    def analyze_weekly_trend(self) -> dict:
        df = self.compute_stats("week")
        if df.empty or "metric_rate" not in df.columns or len(df) < 2:
            return {"direction": "unknown", "slope_per_week": 0.0, "weeks": []}
        df    = df.sort_values("week").reset_index(drop=True)
        rates = df["metric_rate"].values.astype(float)
        slope = float(np.polyfit(range(len(rates)), rates, 1)[0])
        if   slope >  0.1: direction = "improving"
        elif slope < -0.1: direction = "declining"
        else:              direction = "stable"
        return {
            "direction":      direction,
            "slope_per_week": round(slope, 2),
            "weeks":          df.to_dict(orient="records"),
        }

    def summary(self) -> dict:
        df = self.merged()
        if df.empty:
            return {"status": "no_data"}
        return {
            "total_records":         len(df),
            "entity_count":          int(df["entity_id"].nunique()),
            "date_range": {
                "from": str(df["date"].min().date()),
                "to":   str(df["date"].max().date()),
            },
            "metric_rate":           round(float(df["is_positive"].mean() * 100), 1),
            "below_threshold_count": int((~df["is_positive"]).sum()),
            "dimensions":            df["segment"].unique().tolist() if "segment" in df.columns else [],
        }
