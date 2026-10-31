"""Retry + fallback wrapper for Gemini calls.

Busy/rate-limit errors (429, 500, 503...) are retried with backoff, then the next model in
GEMINI_FALLBACK_MODELS (comma-separated, default "gemini-3.5-flash") is tried. Any other
error raises immediately. Drop-in: RetryingClient(genai.Client(...)).models.generate_content(...)
"""
from __future__ import annotations

import os
import time
from typing import Any

TRANSIENT = ("429", "500", "502", "503", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "DEADLINE_EXCEEDED")


def is_transient(e: Exception) -> bool:
    return any(t in str(e) for t in TRANSIENT)


class _Models:
    def __init__(self, inner: Any, fallbacks: list[str], delays: list[float], client: Any = None):
        self.inner, self.fallbacks, self.delays, self._client = inner, fallbacks, delays, client

    def generate_content(self, model: str, contents: Any, config: Any = None, **kw: Any) -> Any:
        last: Exception | None = None
        for m in [model] + [f for f in self.fallbacks if f != model]:
            for i, delay in enumerate([0.0] + self.delays):
                if delay:
                    time.sleep(delay)
                try:
                    return self.inner.generate_content(model=m, contents=contents, config=config, **kw)
                except Exception as e:
                    if not is_transient(e):
                        raise
                    last = e
                    print(f"[llm] {m} busy (try {i + 1}): {str(e)[:60]}")
            print(f"[llm] giving up on {m}")
        assert last is not None
        raise last


class RetryingClient:
    def __init__(self, client: Any, fallbacks: list[str] | None = None, delays: tuple[float, ...] = (3, 8, 20)):
        if fallbacks is None:
            fallbacks = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.5-flash").split(",") if m.strip()]
        self.client = client
        self.models = _Models(client.models, fallbacks, list(delays), client=client)
