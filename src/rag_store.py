from __future__ import annotations

import hashlib
import os

import chromadb
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from .sql_store import _TTLCache


class ExcelsisRAGStore:
    SCHEMA_COLLECTION = "excelsis_schema"
    POLICY_COLLECTION = "excelsis_policy"

    def __init__(
        self,
        chroma_path: str = ".chroma",
        embed_model: str = "BAAI/bge-small-en-v1.5",
        schema_k: int = 6,
        policy_k: int = 4,
    ) -> None:
        self._schema_k = schema_k
        self._policy_k = policy_k
        embeddings = HuggingFaceEmbeddings(model_name=embed_model)

        # A local persistent directory (the default) ties the vector store to
        # one disk and one process — fine for a single instance, but it means
        # every app replica behind a load balancer would build its own
        # separate index. Setting CHROMA_SERVER_HOST points every replica at
        # one shared `chroma run` server instead; unset, behavior is
        # unchanged from the original local-directory mode.
        server_host = os.environ.get("CHROMA_SERVER_HOST", "")
        chroma_kwargs: dict = {}
        if server_host:
            client = chromadb.HttpClient(
                host=server_host,
                port=int(os.environ.get("CHROMA_SERVER_PORT", "8000")),
                ssl=os.environ.get("CHROMA_SERVER_SSL", "false").lower() == "true",
            )
            chroma_kwargs["client"] = client
        else:
            chroma_kwargs["persist_directory"] = chroma_path

        self._schema_vs = Chroma(
            collection_name=self.SCHEMA_COLLECTION,
            embedding_function=embeddings,
            **chroma_kwargs,
        )
        self._policy_vs = Chroma(
            collection_name=self.POLICY_COLLECTION,
            embedding_function=embeddings,
            **chroma_kwargs,
        )
        rag_ttl = int(os.environ.get("RAG_CACHE_TTL", "3600"))
        self._cache = _TTLCache(ttl=rag_ttl, maxsize=256, name="rag")

    def schema_collection(self) -> Chroma:
        return self._schema_vs

    def policy_collection(self) -> Chroma:
        return self._policy_vs

    def _retrieve(self, prefix: str, vs, k: int, not_found: str, query: str) -> str:
        key    = f"{prefix}:{hashlib.sha256(query.encode()).hexdigest()}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        docs   = vs.similarity_search(query, k=k)
        result = not_found if not docs else "\n\n---\n\n".join(d.page_content for d in docs)
        self._cache.set(key, result)
        return result

    def retrieve_schema(self, query: str) -> str:
        return self._retrieve("schema", self._schema_vs, self._schema_k, "No schema information found.", query)

    def retrieve_policy(self, query: str) -> str:
        return self._retrieve("policy", self._policy_vs, self._policy_k, "No policy information found.", query)
