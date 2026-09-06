import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.observability.trace import AgentRunTrace, TraceRecorder, TracingQueryTracker, get_recorder


class TestAgentRunTrace:
    def test_tool_call_counts(self):
        trace = AgentRunTrace(user_id="u1", query="q")
        trace.start_step("retrieve_schema")
        trace.start_step("run_sql_query")
        trace.start_step("run_sql_query")
        assert trace.tool_call_counts() == {"retrieve_schema": 1, "run_sql_query": 2}

    def test_repeated_tools_below_threshold_returns_empty(self):
        trace = AgentRunTrace(user_id="u1", query="q")
        trace.start_step("run_sql_query")
        trace.start_step("run_sql_query")
        assert trace.repeated_tools(threshold=3) == []

    def test_repeated_tools_at_threshold_is_flagged(self):
        trace = AgentRunTrace(user_id="u1", query="q")
        for _ in range(3):
            trace.start_step("run_sql_query")
        assert trace.repeated_tools(threshold=3) == ["run_sql_query"]

    def test_step_duration_recorded_on_end_step(self):
        trace = AgentRunTrace(user_id="u1", query="q")
        step = trace.start_step("retrieve_schema")
        time.sleep(0.01)
        trace.end_step(step)
        assert step.duration_s is not None
        assert step.duration_s > 0

    def test_to_dict_shape(self):
        trace = AgentRunTrace(user_id="u1", query="what is the trend?")
        step = trace.start_step("analyze_trend")
        trace.end_step(step)
        trace.finished_at = trace.started_at + 1.0
        d = trace.to_dict()
        assert d["user_id"] == "u1"
        assert d["query"] == "what is the trend?"
        assert d["tool_call_counts"] == {"analyze_trend": 1}
        assert d["error"] is False
        assert len(d["steps"]) == 1


class TestTraceRecorder:
    def test_recent_returns_most_recent_first(self):
        recorder = TraceRecorder(maxlen=10)
        t1 = AgentRunTrace(user_id="u1", query="first")
        t2 = AgentRunTrace(user_id="u1", query="second")
        recorder.add(t1)
        recorder.add(t2)
        recent = recorder.recent(limit=2)
        assert recent[0].query == "second"
        assert recent[1].query == "first"

    def test_ring_buffer_evicts_oldest(self):
        recorder = TraceRecorder(maxlen=2)
        recorder.add(AgentRunTrace(user_id="u1", query="a"))
        recorder.add(AgentRunTrace(user_id="u1", query="b"))
        recorder.add(AgentRunTrace(user_id="u1", query="c"))
        recent = recorder.recent(limit=10)
        queries = [t.query for t in recent]
        assert "a" not in queries
        assert queries == ["c", "b"]


class _FakePipeline:
    """Minimal double for a redis-py pipeline — only the lpush/ltrim/execute
    surface TraceRecorder actually uses."""

    def __init__(self, store: dict) -> None:
        self._store = store
        self._ops: list[tuple] = []

    def lpush(self, key, value):
        self._ops.append(("lpush", key, value))
        return self

    def ltrim(self, key, start, end):
        self._ops.append(("ltrim", key, start, end))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "lpush":
                _, key, value = op
                self._store.setdefault(key, []).insert(0, value)
            elif op[0] == "ltrim":
                _, key, start, end = op
                values = self._store.get(key, [])
                self._store[key] = values[start: end + 1] if end != -1 else values[start:]
        self._ops.clear()


class _FakeRedis:
    """Minimal in-memory double for the small redis-py surface TraceRecorder
    uses (pipeline/lpush/ltrim/execute, lrange, delete, ping) — lets the
    Redis-backed sharing path be tested without a live Redis/Garnet server."""

    def __init__(self) -> None:
        self._store: dict[str, list[str]] = {}

    def ping(self):
        return True

    def pipeline(self):
        return _FakePipeline(self._store)

    def lrange(self, key, start, end):
        values = self._store.get(key, [])
        return values[start:] if end == -1 else values[start: end + 1]

    def delete(self, key):
        self._store.pop(key, None)


class _BrokenRedis:
    """Simulates a Redis that's unreachable after construction — every call
    raises, so TraceRecorder's best-effort try/except paths can be verified
    to degrade to the in-memory buffer instead of breaking a request."""

    def ping(self):
        return True

    def pipeline(self):
        raise RuntimeError("connection lost")

    def lrange(self, *a, **k):
        raise RuntimeError("connection lost")

    def delete(self, *a, **k):
        raise RuntimeError("connection lost")


class TestTraceRecorderWithRedis:
    def test_add_pushes_to_redis_and_recent_reads_from_it(self):
        recorder = TraceRecorder(maxlen=5, redis_client=_FakeRedis())
        recorder.add(AgentRunTrace(user_id="u1", query="first"))
        recorder.add(AgentRunTrace(user_id="u1", query="second"))

        queries = [t.to_dict()["query"] for t in recorder.recent(limit=10)]
        assert queries == ["second", "first"]

    def test_redis_list_is_capped_at_maxlen(self):
        recorder = TraceRecorder(maxlen=2, redis_client=_FakeRedis())
        for q in ["a", "b", "c"]:
            recorder.add(AgentRunTrace(user_id="u1", query=q))

        queries = [t.to_dict()["query"] for t in recorder.recent(limit=10)]
        assert queries == ["c", "b"]

    def test_clear_deletes_redis_key(self):
        recorder = TraceRecorder(maxlen=5, redis_client=_FakeRedis())
        recorder.add(AgentRunTrace(user_id="u1", query="x"))
        recorder.clear()
        assert recorder.recent(limit=10) == []

    def test_redis_failure_on_add_degrades_to_in_memory_without_raising(self):
        recorder = TraceRecorder(maxlen=5, redis_client=_BrokenRedis())
        recorder.add(AgentRunTrace(user_id="u1", query="still works"))  # must not raise

        recent = recorder.recent(limit=10)  # lrange raises -> falls back to in-memory
        assert recent[0].to_dict()["query"] == "still works"


class TestTracingQueryTracker:
    """TracingQueryTracker feeds the module-level singleton recorder
    (get_recorder()), matching how it's actually wired into src/agent.py —
    each test clears it first so results don't leak between tests."""

    def setup_method(self):
        get_recorder().clear()

    def test_interface_compatible_with_base_tracker_calls(self):
        tracker = TracingQueryTracker()
        tracker.start("u1", "hello")
        tracker.record_tool("get_summary")
        tracker.record_tool_end("get_summary")
        tracker.finish()  # must not raise

    def test_repeated_tool_calls_are_flagged_in_recorded_trace(self):
        tracker = TracingQueryTracker(loop_threshold=3)
        tracker.start("u1", "loopy query")
        for _ in range(3):
            tracker.record_tool("run_sql_query")
            tracker.record_tool_end("run_sql_query")
        tracker.finish()

        traces = get_recorder().recent(limit=1)
        assert traces
        assert "run_sql_query" in traces[0].repeated_tools()

    def test_error_is_recorded_on_trace(self):
        tracker = TracingQueryTracker()
        tracker.start("u1", "will fail")
        tracker.record_error()
        tracker.finish()

        traces = get_recorder().recent(limit=1)
        assert traces[0].error is True

    def test_step_timing_closed_out_by_record_tool_end(self):
        tracker = TracingQueryTracker()
        tracker.start("u1", "timed query")
        tracker.record_tool("retrieve_schema")
        time.sleep(0.01)
        tracker.record_tool_end("retrieve_schema")
        tracker.finish()

        traces = get_recorder().recent(limit=1)
        step = traces[0].steps[0]
        assert step.duration_s is not None
        assert step.duration_s > 0
