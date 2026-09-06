import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.rag_store as rag_store_module
from src.rag_store import ExcelsisRAGStore


class _FakeCollection:
    """Stand-in for the collection object langchain_chroma's Chroma wrapper
    stores after calling get_or_create_collection — nothing beyond
    construction touches it in these tests."""

    def count(self) -> int:
        return 0


class _FakeHttpClient:
    """Records the args it was constructed with instead of connecting
    anywhere — lets the CHROMA_SERVER_HOST branch be verified without a
    live Chroma server. Also stubs get_or_create_collection, which
    langchain_chroma's Chroma wrapper calls immediately on construction."""

    last_kwargs: dict | None = None

    def __init__(self, **kwargs):
        _FakeHttpClient.last_kwargs = kwargs

    def get_or_create_collection(self, **kwargs):
        return _FakeCollection()


class TestExcelsisRAGStoreChromaMode:
    def test_defaults_to_local_persistent_directory_when_unset(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CHROMA_SERVER_HOST", raising=False)
        called = {"http_client": False}
        monkeypatch.setattr(rag_store_module.chromadb, "HttpClient", lambda **kw: called.update(http_client=True))

        ExcelsisRAGStore(chroma_path=str(tmp_path))

        assert called["http_client"] is False

    def test_uses_http_client_when_server_host_is_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CHROMA_SERVER_HOST", "chroma.internal")
        monkeypatch.setenv("CHROMA_SERVER_PORT", "9000")
        monkeypatch.setenv("CHROMA_SERVER_SSL", "true")
        monkeypatch.setattr(rag_store_module.chromadb, "HttpClient", _FakeHttpClient)

        ExcelsisRAGStore(chroma_path=str(tmp_path))

        assert _FakeHttpClient.last_kwargs["host"] == "chroma.internal"
        assert _FakeHttpClient.last_kwargs["port"] == 9000
        assert _FakeHttpClient.last_kwargs["ssl"] is True

    def test_http_client_port_defaults_to_8000(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CHROMA_SERVER_HOST", "chroma.internal")
        monkeypatch.delenv("CHROMA_SERVER_PORT", raising=False)
        monkeypatch.delenv("CHROMA_SERVER_SSL", raising=False)
        monkeypatch.setattr(rag_store_module.chromadb, "HttpClient", _FakeHttpClient)

        ExcelsisRAGStore(chroma_path=str(tmp_path))

        assert _FakeHttpClient.last_kwargs["port"] == 8000
        assert _FakeHttpClient.last_kwargs["ssl"] is False
