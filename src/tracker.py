from __future__ import annotations

import time

from prometheus_client import Counter, Histogram

_prom_tool_invocations: Counter = Counter(
    "agent_tool_invocations_total",
    "Total agent tool invocations by tool name",
    ["tool"],
)
_prom_query_duration: Histogram = Histogram(
    "agent_query_duration_seconds",
    "End-to-end agent query latency in seconds",
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120, 240],
)
_prom_query_errors: Counter = Counter(
    "agent_query_errors_total",
    "Total agent query errors",
)


class QueryTracker:
    """Per-request Prometheus tracker. finish() is idempotent."""

    def __init__(self) -> None:
        self._start_time: float = 0.0

    def start(self, user_id: str, query: str) -> None:
        self._start_time = time.monotonic()

    def record_tool(self, tool_name: str) -> None:
        _prom_tool_invocations.labels(tool=tool_name).inc()

    def record_tool_end(self, tool_name: str) -> None:
        """No-op here; overridden by TracingQueryTracker to close out a step's
        timing. Kept on the base class so callers can invoke it unconditionally
        regardless of which tracker implementation is in use."""

    def record_error(self) -> None:
        _prom_query_errors.inc()

    def finish(self) -> None:
        if self._start_time:
            _prom_query_duration.observe(time.monotonic() - self._start_time)
            self._start_time = 0.0
