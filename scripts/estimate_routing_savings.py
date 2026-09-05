#!/usr/bin/env python3
"""
Estimate the cost impact of tiered model routing against a representative
sample of chat queries, using `classify_complexity` from `src.routing.model_router`.

This does not call any model — it only classifies queries and multiplies by
illustrative per-1k-token pricing, so it runs instantly with no Ollama or
network dependency. Swap SAMPLE_QUERIES for a real logged-query sample and
the cost constants for your actual provider's pricing to get a real estimate
for your deployment.

Usage:
    python scripts/estimate_routing_savings.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.routing.model_router import classify_complexity

# Illustrative queries spanning the kinds of things users actually ask this
# agent (see README's example questions) — swap in a real sample of logged
# queries for an estimate that reflects your actual traffic mix.
SAMPLE_QUERIES = [
    "Hello, what can you help me with?",
    "What's the overall metric rate?",
    "List all entities below 70% threshold",
    "Which groups have the lowest metric rate this month?",
    "How does this week compare to last month?",
    "Show me the trend, is it improving or declining?",
    "Why is the finance segment underperforming and what should we recommend?",
    "Compare segment A and segment B over the last 30 days",
    "What's the weekly trend for group C versus the overall average?",
    "Give me a summary",
    "Explain why the anomaly detector flagged entity E042",
    "What is the at-risk threshold?",
]

# Illustrative pricing — replace with your actual local vs. cloud-tier costs.
LOCAL_COST_PER_1K = 0.0        # local Ollama: no per-token cost
CLOUD_COST_PER_1K = 0.003      # example only — set to your real cloud-tier rate
AVG_TOKENS_PER_QUERY = 800     # rough end-to-end estimate (prompt + tool results + completion)


def main() -> None:
    simple = 0
    complex_ = 0
    for q in SAMPLE_QUERIES:
        if classify_complexity(q) == "complex":
            complex_ += 1
        else:
            simple += 1

    total = simple + complex_
    baseline_cost = total * (AVG_TOKENS_PER_QUERY / 1000.0) * CLOUD_COST_PER_1K
    routed_cost = (
        simple * (AVG_TOKENS_PER_QUERY / 1000.0) * LOCAL_COST_PER_1K
        + complex_ * (AVG_TOKENS_PER_QUERY / 1000.0) * CLOUD_COST_PER_1K
    )
    savings_pct = (1 - routed_cost / baseline_cost) * 100 if baseline_cost else 0.0

    print(f"Sample size            : {total} queries")
    print(f"Routed to local tier   : {simple} ({simple / total:.0%})")
    print(f"Routed to cloud tier   : {complex_} ({complex_ / total:.0%})")
    print()
    print(f"Baseline (all-cloud)   : ${baseline_cost:.4f}")
    print(f"Routed                 : ${routed_cost:.4f}")
    print(f"Estimated savings      : {savings_pct:.0f}%")
    print()
    print("Note: pricing constants above are illustrative placeholders — set")
    print("LOCAL_COST_PER_1K / CLOUD_COST_PER_1K to your actual provider rates.")


if __name__ == "__main__":
    main()
