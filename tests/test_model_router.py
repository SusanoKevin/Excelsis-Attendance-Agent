import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.routing.model_router import ModelRouter, ModelTier, classify_complexity


class TestClassifyComplexity:
    def test_greeting_is_simple(self):
        assert classify_complexity("Hello, what can you help me with?") == "simple"

    def test_short_direct_lookup_is_simple(self):
        assert classify_complexity("What is the overall metric rate?") == "simple"

    def test_comparison_is_complex(self):
        assert classify_complexity("Compare segment A and segment B") == "complex"

    def test_why_question_is_complex(self):
        assert classify_complexity("Why is the finance segment underperforming?") == "complex"

    def test_multiple_question_marks_is_complex(self):
        assert classify_complexity("What is the trend? Is it improving?") == "complex"

    def test_very_long_query_is_complex(self):
        long_query = " ".join(["word"] * 41)
        assert classify_complexity(long_query) == "complex"


class TestModelTier:
    def test_build_is_called_lazily_and_only_once(self):
        calls = []

        def build():
            calls.append(1)
            return object()

        tier = ModelTier(name="local", cost_per_1k_tokens=0.0, build=build)
        assert calls == []  # not built at construction time
        instance1 = tier.get()
        instance2 = tier.get()
        assert calls == [1]  # only built once
        assert instance1 is instance2


class TestModelRouter:
    def test_simple_query_routes_to_simple_tier(self):
        simple = ModelTier(name="local", cost_per_1k_tokens=0.0, build=lambda: "local-model")
        complex_ = ModelTier(name="cloud", cost_per_1k_tokens=0.003, build=lambda: "cloud-model")
        router = ModelRouter(simple_tier=simple, complex_tier=complex_)

        model, tier_name = router.select("Hello, what can you help me with?")
        assert model == "local-model"
        assert tier_name == "local"

    def test_complex_query_routes_to_complex_tier(self):
        simple = ModelTier(name="local", cost_per_1k_tokens=0.0, build=lambda: "local-model")
        complex_ = ModelTier(name="cloud", cost_per_1k_tokens=0.003, build=lambda: "cloud-model")
        router = ModelRouter(simple_tier=simple, complex_tier=complex_)

        model, tier_name = router.select("Compare this month and last month, and explain why it changed")
        assert model == "cloud-model"
        assert tier_name == "cloud"

    def test_falls_back_to_single_tier_when_no_complex_tier_given(self):
        only_tier = ModelTier(name="local", cost_per_1k_tokens=0.0, build=lambda: "local-model")
        router = ModelRouter(simple_tier=only_tier)

        _, simple_name = router.select("Hello")
        _, complex_name = router.select("Compare A and B and explain why")
        assert simple_name == "local"
        assert complex_name == "local"

    def test_record_usage_does_not_raise(self):
        tier = ModelTier(name="local", cost_per_1k_tokens=0.0, build=lambda: "local-model")
        router = ModelRouter(simple_tier=tier)
        router.record_usage("local", estimated_tokens=500, latency_s=1.2, cost_per_1k_tokens=0.0)
        router.record_usage("cloud", estimated_tokens=500, latency_s=2.0, cost_per_1k_tokens=0.003)
