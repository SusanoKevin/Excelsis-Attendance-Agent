from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from prometheus_client import Counter, Histogram

_prom_tier_selected: Counter = Counter(
    "agent_model_tier_selected_total",
    "Total queries routed to each model tier",
    ["tier"],
)
_prom_tier_cost: Counter = Counter(
    "agent_estimated_cost_usd_total",
    "Estimated cumulative inference cost in USD by model tier",
    ["tier"],
)
_prom_tier_latency: Histogram = Histogram(
    "agent_model_tier_latency_seconds",
    "Observed latency by model tier",
    ["tier"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120],
)

# Heuristic signals that a query needs the stronger tier: multi-part
# reasoning, comparisons, or explicit analytical asks. This is a fast,
# fully local, explainable classifier — not a learned model — so routing
# never costs a second LLM round-trip before the "real" one.
_COMPLEX_MARKERS = (
    "compare", "why", "trend", "correlat", "predict", "forecast",
    "explain", "recommend", " and ", " vs ", " versus ",
)
_SIMPLE_WORD_LIMIT = 40


@dataclass
class ModelTier:
    """One routable model tier. `build` lazily constructs the actual chat
    model (e.g. a `ChatOllama` instance) so importing this module never
    requires a model or network connection to exist."""

    name: str
    cost_per_1k_tokens: float  # USD; 0.0 for a locally-hosted model
    build: Callable[[], object]
    _instance: object | None = field(default=None, init=False, repr=False)

    def get(self):
        if self._instance is None:
            self._instance = self.build()
        return self._instance


def classify_complexity(query: str) -> str:
    """Cheap, explainable heuristic — no model call. Returns 'simple' or
    'complex'. Multi-clause, comparative, or analytical questions route to
    the stronger tier; short, direct lookups and greetings route to the
    cheap/fast tier."""
    q = query.lower()
    if len(q.split()) > _SIMPLE_WORD_LIMIT:
        return "complex"
    if any(marker in q for marker in _COMPLEX_MARKERS):
        return "complex"
    if q.count("?") > 1:
        return "complex"
    return "simple"


class ModelRouter:
    """Routes a query to the cheapest/fastest model tier likely to handle it
    well, tracking estimated cost and latency per tier via Prometheus.

    Two tiers are typical: a fast/free local model for direct lookups and
    greetings, and a stronger (optionally cloud) model for multi-step
    analytical questions. Falls back to a single tier gracefully if only one
    is configured (e.g. no cloud tier available) — `select()` always returns
    a usable model either way.
    """

    def __init__(self, simple_tier: ModelTier, complex_tier: ModelTier | None = None) -> None:
        self._simple = simple_tier
        self._complex = complex_tier or simple_tier

    def select(self, query: str) -> tuple[object, str]:
        """Returns (model_instance, tier_name)."""
        complexity = classify_complexity(query)
        tier = self._complex if complexity == "complex" else self._simple
        _prom_tier_selected.labels(tier=tier.name).inc()
        return tier.get(), tier.name

    def record_usage(self, tier_name: str, estimated_tokens: int, latency_s: float, cost_per_1k_tokens: float) -> None:
        _prom_tier_latency.labels(tier=tier_name).observe(latency_s)
        if cost_per_1k_tokens:
            _prom_tier_cost.labels(tier=tier_name).inc((estimated_tokens / 1000.0) * cost_per_1k_tokens)
