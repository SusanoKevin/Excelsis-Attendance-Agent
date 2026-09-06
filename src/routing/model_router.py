from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
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
class ReplicaHandle:
    """One backing model instance behind a tier (e.g. one Ollama endpoint).
    `build` lazily constructs it so importing this module never requires a
    model or network connection to exist."""

    name: str
    build: Callable[[], object]
    _instance: object | None = field(default=None, init=False, repr=False)

    def get(self) -> object:
        if self._instance is None:
            self._instance = self.build()
        return self._instance


class ReplicaPool:
    """Round-robins across multiple backing instances for one tier — e.g.
    several Ollama endpoints behind the same logical "simple" tier — so a
    single model process is not a hard concurrency ceiling for that tier.

    Round-robin needs no per-call release step (unlike a least-loaded
    strategy, which would require tracking in-flight requests per replica);
    that keeps this safe to use without a matching "done" call anywhere a
    tier is selected.
    """

    def __init__(self, replicas: list[ReplicaHandle]) -> None:
        if not replicas:
            raise ValueError("ReplicaPool requires at least one replica")
        self._replicas = replicas
        self._next_index = 0
        self._lock = Lock()

    def acquire(self) -> ReplicaHandle:
        with self._lock:
            replica = self._replicas[self._next_index % len(self._replicas)]
            self._next_index += 1
            return replica

    def __len__(self) -> int:
        return len(self._replicas)


@dataclass
class ModelTier:
    """One routable model tier, backed by either a single lazily-built
    instance (`build`) or a `ReplicaPool` of several (`replicas`) — pass
    exactly one of the two. The pooled form lets one tier scale across
    multiple model-serving replicas without the caller needing to know
    which replica actually handled a given call."""

    name: str
    cost_per_1k_tokens: float  # USD; 0.0 for a locally-hosted model
    build: Callable[[], object] | None = None
    replicas: ReplicaPool | None = None
    _instance: object | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if (self.build is None) == (self.replicas is None):
            raise ValueError("ModelTier requires exactly one of `build` or `replicas`")

    def get(self) -> object:
        if self.replicas is not None:
            return self.replicas.acquire().get()
        if self._instance is None:
            self._instance = self.build()
        return self._instance

    @property
    def replica_count(self) -> int:
        return len(self.replicas) if self.replicas is not None else 1


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
