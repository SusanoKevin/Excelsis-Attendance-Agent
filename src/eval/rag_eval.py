from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class RAGEvalCase:
    """One golden-set test case for retrieval-quality evaluation.

    `expected_substrings` is a list of alternatives — a hit counts if ANY one
    of them appears (case-insensitive) in a retrieved chunk, since a fact is
    often phrased more than one way across the source documents.
    """

    query: str
    expected_substrings: list[str]
    k: int = 4
    min_relevance: float = 0.0
    tags: list[str] = field(default_factory=list)


@dataclass
class RAGEvalResult:
    case: RAGEvalCase
    hit: bool
    rank_of_first_hit: Optional[int]
    top_score: float
    retrieved_previews: list[str]
    latency_s: float


@dataclass
class RAGEvalReport:
    results: list[RAGEvalResult]

    @property
    def hit_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.hit) / len(self.results)

    @property
    def mean_reciprocal_rank(self) -> float:
        if not self.results:
            return 0.0
        total = sum(1.0 / r.rank_of_first_hit for r in self.results if r.rank_of_first_hit)
        return total / len(self.results)

    @property
    def mean_latency_s(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.latency_s for r in self.results) / len(self.results)

    @property
    def failures(self) -> list[RAGEvalResult]:
        return [r for r in self.results if not r.hit]

    def to_dict(self) -> dict:
        return {
            "num_cases": len(self.results),
            "hit_rate": round(self.hit_rate, 4),
            "mean_reciprocal_rank": round(self.mean_reciprocal_rank, 4),
            "mean_latency_s": round(self.mean_latency_s, 4),
            "num_failures": len(self.failures),
            "failures": [
                {
                    "query": r.case.query,
                    "expected_one_of": r.case.expected_substrings,
                    "retrieved_previews": r.retrieved_previews,
                }
                for r in self.failures
            ],
        }

    def summary(self) -> str:
        lines = [
            f"RAG retrieval eval — {len(self.results)} cases",
            f"  hit@k        : {self.hit_rate:.1%}",
            f"  MRR          : {self.mean_reciprocal_rank:.3f}",
            f"  mean latency : {self.mean_latency_s * 1000:.0f} ms",
        ]
        if self.failures:
            lines.append(f"  FAILURES ({len(self.failures)}):")
            for r in self.failures:
                lines.append(f"    - {r.case.query!r} -> expected one of {r.case.expected_substrings}")
        return "\n".join(lines)


def evaluate_case(vectorstore, case: RAGEvalCase) -> RAGEvalResult:
    """Run one retrieval-quality test case against a Chroma-compatible vectorstore.

    Deliberately avoids an LLM call — this measures retrieval quality alone
    (did the right chunk come back, and how high did it rank), which is fast,
    deterministic, and safe to run in CI on every schema/docs change. See
    `src/eval/judge.py` for an optional, LLM-based generation-groundedness
    check layered on top of this.
    """
    start = time.monotonic()
    docs_and_scores = vectorstore.similarity_search_with_relevance_scores(case.query, k=case.k)
    latency = time.monotonic() - start

    rank_of_first_hit = None
    for i, (doc, _score) in enumerate(docs_and_scores, start=1):
        if any(sub.lower() in doc.page_content.lower() for sub in case.expected_substrings):
            rank_of_first_hit = i
            break

    top_score = docs_and_scores[0][1] if docs_and_scores else 0.0
    hit = rank_of_first_hit is not None and top_score >= case.min_relevance

    return RAGEvalResult(
        case=case,
        hit=hit,
        rank_of_first_hit=rank_of_first_hit,
        top_score=top_score,
        retrieved_previews=[doc.page_content[:120] for doc, _ in docs_and_scores],
        latency_s=latency,
    )


def run_eval(vectorstore, cases: list[RAGEvalCase]) -> RAGEvalReport:
    return RAGEvalReport(results=[evaluate_case(vectorstore, c) for c in cases])


def load_golden_set(path: str | Path) -> list[RAGEvalCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RAGEvalCase(**item) for item in data]
