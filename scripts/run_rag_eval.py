#!/usr/bin/env python3
"""
Run the RAG retrieval-quality golden set against the live policy vector store.

Measures hit@k and mean reciprocal rank for a fixed set of known-answer
questions against docs/data_policy.md as indexed in Chroma — catches
retrieval regressions whenever the policy docs or chunking/embedding config
change, without needing Ollama or a live SQL Server connection.

Usage:
    python scripts/run_rag_eval.py
    python scripts/run_rag_eval.py --golden-set src/eval/golden_sets/policy_golden_set.json
    python scripts/run_rag_eval.py --json report.json
    python scripts/run_rag_eval.py --llm-judge   # also scores generation groundedness (requires Ollama)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from src.eval.rag_eval import load_golden_set, run_eval
from src.rag_store import ExcelsisRAGStore

_DEFAULT_GOLDEN_SET = Path(__file__).parent.parent / "src" / "eval" / "golden_sets" / "policy_golden_set.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden-set", default=str(_DEFAULT_GOLDEN_SET), help="Path to a golden-set JSON file")
    parser.add_argument("--json", default=None, help="Optional path to write the full report as JSON")
    parser.add_argument(
        "--llm-judge",
        action="store_true",
        help="Also run an LLM-judge groundedness pass on each case's top retrieved chunk (requires Ollama)",
    )
    parser.add_argument("--fail-under", type=float, default=None, help="Exit non-zero if hit_rate falls below this (0-1)")
    args = parser.parse_args()

    store = ExcelsisRAGStore(
        chroma_path=os.getenv("CHROMA_PATH", ".chroma"),
        embed_model=os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
    )
    cases = load_golden_set(args.golden_set)
    report = run_eval(store.policy_collection(), cases)

    print(report.summary())

    if args.llm_judge:
        from langchain_ollama import ChatOllama

        from src.eval.judge import judge_groundedness

        llm = ChatOllama(model=os.environ.get("MODEL", "qwen2.5:14b"), temperature=0.0)
        print("\nGroundedness (LLM-judge, top retrieved chunk as context):")
        for result in report.results:
            context = result.retrieved_previews[0] if result.retrieved_previews else ""
            # No live "generated answer" here — judge the retrieved context against
            # itself as a sanity smoke test that the judge call/parsing works; wire
            # a real agent-generated answer in for production use.
            verdict = judge_groundedness(llm, context=context, answer=context)
            status = "OK" if verdict.grounded else f"FLAG ({verdict.reason})"
            print(f"  - {result.case.query!r}: {status}")

    if args.json:
        Path(args.json).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"\nFull report written to {args.json}")

    if args.fail_under is not None and report.hit_rate < args.fail_under:
        print(f"\nFAIL: hit_rate {report.hit_rate:.1%} is below threshold {args.fail_under:.1%}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
