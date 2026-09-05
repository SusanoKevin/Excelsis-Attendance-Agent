import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from src.eval.rag_eval import RAGEvalCase, evaluate_case, run_eval


class _StubVectorStore:
    """Returns a fixed, caller-controlled ranked list of (Document, score)
    pairs per query — lets the eval harness's own scoring/aggregation logic
    (hit detection, rank-of-first-hit, MRR, min_relevance gating) be tested
    deterministically, independent of any real embedding model's actual
    semantic ranking behavior."""

    def __init__(self, mapping: dict[str, list[tuple[str, float]]]):
        self._mapping = mapping

    def similarity_search_with_relevance_scores(self, query: str, k: int = 4):
        ranked = self._mapping.get(query, [])
        return [(Document(page_content=text), score) for text, score in ranked[:k]]


class TestEvaluateCase:
    def test_hit_at_rank_one(self):
        store = _StubVectorStore({"q": [("policy: one Absent for reporting", 0.9), ("unrelated", 0.5)]})
        case = RAGEvalCase(query="q", expected_substrings=["one Absent"], k=2)
        result = evaluate_case(store, case)
        assert result.hit is True
        assert result.rank_of_first_hit == 1

    def test_hit_at_rank_two(self):
        store = _StubVectorStore({"q": [("unrelated", 0.9), ("AT_RISK_THRESHOLD defaults to 75", 0.5)]})
        case = RAGEvalCase(query="q", expected_substrings=["AT_RISK_THRESHOLD"], k=2)
        result = evaluate_case(store, case)
        assert result.hit is True
        assert result.rank_of_first_hit == 2

    def test_miss_when_expected_substring_absent(self):
        store = _StubVectorStore({"q": [("unrelated text", 0.9)]})
        case = RAGEvalCase(query="q", expected_substrings=["this text does not exist anywhere"], k=3)
        result = evaluate_case(store, case)
        assert result.hit is False
        assert result.rank_of_first_hit is None

    def test_any_of_multiple_expected_substrings_counts_as_hit(self):
        store = _StubVectorStore({"q": [("contains AT_RISK_THRESHOLD only", 0.9)]})
        case = RAGEvalCase(query="q", expected_substrings=["not present anywhere", "AT_RISK_THRESHOLD"], k=3)
        result = evaluate_case(store, case)
        assert result.hit is True

    def test_min_relevance_gates_an_otherwise_matching_top_hit(self):
        store = _StubVectorStore({"q": [("has one Absent in it", 0.1)]})
        case = RAGEvalCase(query="q", expected_substrings=["one Absent"], k=1, min_relevance=0.5)
        result = evaluate_case(store, case)
        assert result.hit is False

    def test_no_results_is_a_clean_miss(self):
        store = _StubVectorStore({})
        case = RAGEvalCase(query="q", expected_substrings=["anything"], k=3)
        result = evaluate_case(store, case)
        assert result.hit is False
        assert result.top_score == 0.0
        assert result.retrieved_previews == []


class TestRunEvalReport:
    def test_report_aggregates_hit_rate_and_mrr(self):
        store = _StubVectorStore({
            "a": [("has one Absent", 0.9)],
            "b": [("irrelevant content", 0.9)],
        })
        cases = [
            RAGEvalCase(query="a", expected_substrings=["one Absent"], k=3),
            RAGEvalCase(query="b", expected_substrings=["nonexistent phrase"], k=3),
        ]
        report = run_eval(store, cases)
        assert len(report.results) == 2
        assert report.hit_rate == 0.5
        assert report.mean_reciprocal_rank == 0.5  # 1.0 for "a" (rank 1) + 0 for "b", averaged over 2
        assert len(report.failures) == 1
        assert report.failures[0].case.query == "b"

    def test_empty_report_does_not_divide_by_zero(self):
        report = run_eval(_StubVectorStore({}), [])
        assert report.hit_rate == 0.0
        assert report.mean_reciprocal_rank == 0.0
        assert report.mean_latency_s == 0.0

    def test_summary_and_to_dict_are_well_formed(self):
        store = _StubVectorStore({"a": [("has one Absent", 0.9)]})
        cases = [RAGEvalCase(query="a", expected_substrings=["one Absent"], k=3)]
        report = run_eval(store, cases)
        assert "hit@k" in report.summary()
        d = report.to_dict()
        assert d["num_cases"] == 1
        assert d["num_failures"] == 0


class TestRealChromaSmoke:
    """One end-to-end check against a genuine Chroma collection (fake
    embeddings, no network) confirming evaluate_case works through the real
    `similarity_search_with_relevance_scores` interface, not just the stub
    above. Does not assert on ranking correctness — DeterministicFakeEmbedding
    has no real semantic notion of similarity, so only shape/plumbing is
    checked here."""

    def test_evaluate_case_runs_against_real_chroma_without_error(self):
        embeddings = DeterministicFakeEmbedding(size=32)
        docs = [Document(page_content="Weekly reports run Monday through Sunday.")]
        store = Chroma.from_documents(docs, embedding=embeddings, collection_name=f"test_{uuid.uuid4().hex}")
        case = RAGEvalCase(query="weekly reporting schedule", expected_substrings=["Monday"], k=1)
        result = evaluate_case(store, case)
        assert isinstance(result.hit, bool)
        assert len(result.retrieved_previews) == 1
