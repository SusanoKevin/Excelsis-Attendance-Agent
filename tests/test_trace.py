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
