import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

import src.agent as agent_module
from src.agent import ExcelsisAgent, _build_checkpointer, _CHECKPOINT_TABLES


class _FakePostgresConnection:
    """Records connect() kwargs instead of reaching a real Postgres server."""

    last_kwargs: dict | None = None

    def __init__(self, **kwargs):
        _FakePostgresConnection.last_kwargs = kwargs


class _FakePsycopg:
    def __init__(self):
        self.connect_uri = None

    def connect(self, uri, **kwargs):
        self.connect_uri = uri
        return _FakePostgresConnection(**kwargs)


class _FakePostgresSaver:
    """Stand-in for langgraph.checkpoint.postgres.PostgresSaver — records
    the connection it was built with and whether setup() ran, without
    needing a live Postgres server."""

    last_conn = None
    setup_calls = 0

    def __init__(self, conn):
        self.conn = conn
        _FakePostgresSaver.last_conn = conn

    def setup(self):
        _FakePostgresSaver.setup_calls += 1


class TestBuildCheckpointer:
    def test_defaults_to_sqlite_when_uri_is_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CHAT_DB", str(tmp_path / "chat.db"))
        checkpointer = _build_checkpointer("")
        assert isinstance(checkpointer, SqliteSaver)

    def test_uses_postgres_saver_when_uri_given(self, monkeypatch):
        fake_psycopg = _FakePsycopg()
        monkeypatch.setattr(agent_module, "psycopg", fake_psycopg)
        monkeypatch.setattr(agent_module, "PostgresSaver", _FakePostgresSaver)
        _FakePostgresSaver.setup_calls = 0

        checkpointer = _build_checkpointer("postgresql://user:pass@host/db")

        assert isinstance(checkpointer, _FakePostgresSaver)
        assert fake_psycopg.connect_uri == "postgresql://user:pass@host/db"
        assert _FakePostgresSaver.setup_calls == 1

    def test_raises_when_uri_set_but_package_missing(self, monkeypatch):
        monkeypatch.setattr(agent_module, "PostgresSaver", None)
        with pytest.raises(RuntimeError, match="not installed"):
            _build_checkpointer("postgresql://host/db")


class TestClearThreadDispatch:
    def test_sqlite_uri_unset_uses_sqlite_clear_path(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CHECKPOINT_DB_URI", raising=False)
        monkeypatch.setenv("CHAT_DB", str(tmp_path / "chat.db"))
        agent = ExcelsisAgent(checkpointer=InMemorySaver())

        calls = []
        agent._clear_thread_sqlite = lambda thread_id: calls.append(("sqlite", thread_id))
        agent._clear_thread_postgres = lambda thread_id: calls.append(("postgres", thread_id))

        agent._clear_thread("user-1")

        assert calls == [("sqlite", "user-1")]

    def test_checkpoint_db_uri_set_uses_postgres_clear_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CHECKPOINT_DB_URI", "postgresql://host/db")
        agent = ExcelsisAgent(checkpointer=InMemorySaver())

        calls = []
        agent._clear_thread_sqlite = lambda thread_id: calls.append(("sqlite", thread_id))
        agent._clear_thread_postgres = lambda thread_id: calls.append(("postgres", thread_id))

        agent._clear_thread("user-1")

        assert calls == [("postgres", "user-1")]


class _FakeCursor:
    def __init__(self, executed: list) -> None:
        self._executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self._executed.append((sql, params))


class _FakePostgresConnForClear:
    def __init__(self, executed: list) -> None:
        self._executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._executed)


class TestClearThreadPostgres:
    def test_deletes_from_every_checkpoint_table(self, monkeypatch):
        monkeypatch.setenv("CHECKPOINT_DB_URI", "postgresql://host/db")
        agent = ExcelsisAgent(checkpointer=InMemorySaver())

        executed: list = []
        monkeypatch.setattr(
            agent_module.psycopg, "connect", lambda uri, **kw: _FakePostgresConnForClear(executed)
        )

        agent._clear_thread_postgres("user-1")

        touched_tables = {sql.split()[2] for sql, _params in executed}
        assert touched_tables == set(_CHECKPOINT_TABLES)
        assert all(params == ("user-1",) for _sql, params in executed)
