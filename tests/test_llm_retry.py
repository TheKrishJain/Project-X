"""Unit tests for llm_retry wrapper (404-skip, per-day-skip, retry backoff)."""
from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from src.llm_retry import RetryingClient


class FakeModels:
    def __init__(self, side_effects: dict[str, list[Any]]):
        self.side_effects = side_effects
        self.calls: list[str] = []

    def generate_content(self, model: str, contents: Any, config: Any = None, **kw: Any) -> Any:
        self.calls.append(model)
        effects = self.side_effects.get(model, [])
        if not effects:
            return f"success from {model}"
        val = effects.pop(0)
        if isinstance(val, Exception):
            raise val
        return val


class FakeClient:
    def __init__(self, side_effects: dict[str, list[Any]]):
        self.models = FakeModels(side_effects)


class TestLLMRetry(unittest.TestCase):
    def test_404_skip_to_next_model(self):
        fake = FakeClient({
            "model-404": [Exception("404 NOT_FOUND. Model models/model-404 is not found")],
            "model-fallback": ["fallback-success"],
        })
        sleeps: list[float] = []
        with patch("time.sleep", side_effect=sleeps.append):
            client = RetryingClient(fake, fallbacks=["model-fallback"], delays=(3, 8, 20))
            res = client.models.generate_content(model="model-404", contents="prompt")

        self.assertEqual(res, "fallback-success")
        self.assertEqual(fake.models.calls, ["model-404", "model-fallback"])
        self.assertEqual(sleeps, [])

    def test_per_day_skip_no_sleep(self):
        fake = FakeClient({
            "model-daily": [Exception("429 RESOURCE_EXHAUSTED. Generate content requests per day limit exceeded on Free Tier")],
            "model-ok": ["ok-success"],
        })
        sleeps: list[float] = []
        with patch("time.sleep", side_effect=sleeps.append):
            client = RetryingClient(fake, fallbacks=["model-ok"], delays=(3, 8, 20))
            res = client.models.generate_content(model="model-daily", contents="prompt")

        self.assertEqual(res, "ok-success")
        self.assertEqual(fake.models.calls, ["model-daily", "model-ok"])
        self.assertEqual(sleeps, [])

    def test_all_models_daily_exhausted_raises(self):
        fake = FakeClient({
            "model-1": [Exception("429 RESOURCE_EXHAUSTED. PerDay limit exceeded")],
            "model-2": [Exception("429 RESOURCE_EXHAUSTED. Requests per day limit exceeded")],
        })
        sleeps: list[float] = []
        with patch("time.sleep", side_effect=sleeps.append):
            client = RetryingClient(fake, fallbacks=["model-2"], delays=(3, 8, 20))
            with self.assertRaises(RuntimeError) as ctx:
                client.models.generate_content(model="model-1", contents="prompt")
            self.assertIn("Gemini daily quota exhausted", str(ctx.exception))

        self.assertEqual(fake.models.calls, ["model-1", "model-2"])
        self.assertEqual(sleeps, [])

    def test_per_minute_429_retries_with_waits(self):
        fake = FakeClient({
            "model-pm": [
                Exception("429 RESOURCE_EXHAUSTED. Rate limit exceeded for RPM"),
                "rpm-recovered",
            ]
        })
        sleeps: list[float] = []
        with patch("time.sleep", side_effect=sleeps.append):
            client = RetryingClient(fake, fallbacks=["fallback"], rate_limit_delays=(20, 45, 90))
            res = client.models.generate_content(model="model-pm", contents="prompt")

        self.assertEqual(res, "rpm-recovered")
        self.assertEqual(sleeps, [20.0])


if __name__ == "__main__":
    unittest.main()
