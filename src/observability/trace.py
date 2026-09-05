from __future__ import annotations

import time
from collections import Counter as _Counter, deque
from dataclasses import dataclass, field
from threading import Lock

from prometheus_client import Counter, Histogram

from ..tracker import QueryTracker

_prom_repeated_calls: Counter = Counter(
    "agent_repeated_tool_calls_total",
    "Total times a query repeated the same tool call at or beyond the loop threshold",
)
_prom_step_duration: Histogram = Histogram(
    "agent_step_duration_seconds",
    "Per-tool-call latency within a single agent run",
    ["tool"],
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 30],
)

_DEFAULT_LOOP_THRESHOLD = 3


@dataclass
class ToolStep:
    tool: str
    start_ts: float
    end_ts: float | None = None

    @property
    def duration_s(self) -> float | None:
        if self.end_ts is None:
            return None
        return self.end_ts - self.start_ts


@dataclass
class AgentRunTrace:
    """Records every tool step of a single agent run for post-hoc inspection.

    LangGraph's own `GraphRecursionError` is a hard backstop against runaway
    loops, but it only fires after the recursion limit is exhausted — by then
    the run has already burned its full latency/token budget. This trace lets
    the same "same tool called repeatedly" signal be caught (and alerted on)
    well before that hard limit, and gives per-step timing for diagnosing
    which specific tool is slow rather than just the end-to-end duration.
    """

    user_id: str
    query: str
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    steps: list[ToolStep] = field(default_factory=list)
    error: bool = False

    def start_step(self, tool: str) -> ToolStep:
        step = ToolStep(tool=tool, start_ts=time.monotonic())
        self.steps.append(step)
        return step

    def end_step(self, step: ToolStep) -> None:
        step.end_ts = time.monotonic()
        if step.duration_s is not None:
            _prom_step_duration.labels(tool=step.tool).observe(step.duration_s)

    def tool_call_counts(self) -> dict[str, int]:
        return dict(_Counter(s.tool for s in self.steps))

    def repeated_tools(self, threshold: int = _DEFAULT_LOOP_THRESHOLD) -> list[str]:
        """Tool names called >= `threshold` times in this run — a loop signal."""
        return [tool for tool, count in self.tool_call_counts().items() if count >= threshold]

    @property
    def total_duration_s(self) -> float | None:
        if self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "query": self.query,
            "total_duration_s": self.total_duration_s,
            "tool_call_counts": self.tool_call_counts(),
            "repeated_tools": self.repeated_tools(),
            "error": self.error,
            "steps": [{"tool": s.tool, "duration_s": s.duration_s} for s in self.steps],
        }


class TraceRecorder:
    """Thread-safe ring buffer of the most recent agent run traces."""

    def __init__(self, maxlen: int = 200) -> None:
        self._traces: deque[AgentRunTrace] = deque(maxlen=maxlen)
        self._lock = Lock()

    def add(self, trace: AgentRunTrace) -> None:
        with self._lock:
            self._traces.append(trace)

    def recent(self, limit: int = 50) -> list[AgentRunTrace]:
        with self._lock:
            items = list(self._traces)[-limit:]
        return list(reversed(items))

    def clear(self) -> None:
        with self._lock:
            self._traces.clear()


_recorder = TraceRecorder()


def get_recorder() -> TraceRecorder:
    return _recorder


class TracingQueryTracker(QueryTracker):
    """Drop-in replacement for QueryTracker that additionally records a full
    per-tool-call trace for reliability inspection (loop/error detection),
    on top of the existing aggregate Prometheus counters.

    Interface-compatible with QueryTracker (start/record_tool/record_error/
    finish) plus one new hook (record_tool_end) — safe to substitute wherever
    the plain tracker is used today.
    """

    def __init__(self, loop_threshold: int = _DEFAULT_LOOP_THRESHOLD) -> None:
        super().__init__()
        self._loop_threshold = loop_threshold
        self._trace: AgentRunTrace | None = None
        self._open_steps: dict[str, ToolStep] = {}

    def start(self, user_id: str, query: str) -> None:
        super().start(user_id, query)
        self._trace = AgentRunTrace(user_id=user_id, query=query)

    def record_tool(self, tool_name: str) -> None:
        super().record_tool(tool_name)
        if self._trace is None:
            return
        step = self._trace.start_step(tool_name)
        self._open_steps[tool_name] = step
        if tool_name in self._trace.repeated_tools(self._loop_threshold):
            _prom_repeated_calls.inc()

    def record_tool_end(self, tool_name: str) -> None:
        super().record_tool_end(tool_name)
        step = self._open_steps.pop(tool_name, None)
        if step is not None and self._trace is not None:
            self._trace.end_step(step)

    def record_error(self) -> None:
        super().record_error()
        if self._trace is not None:
            self._trace.error = True

    def finish(self) -> None:
        super().finish()
        if self._trace is not None:
            self._trace.finished_at = time.monotonic()
            _recorder.add(self._trace)
            self._trace = None
            self._open_steps.clear()
