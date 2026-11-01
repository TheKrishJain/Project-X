"""Retry + fallback wrapper for Gemini calls.

Busy/rate-limit errors (429, 500, 503...) are retried with backoff, then the next model in
GEMINI_FALLBACK_MODELS (comma-separated, default "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-2.5-flash,gemini-2.5-flash-lite")
is tried. Any other error raises immediately. Drop-in: RetryingClient(genai.Client(...)).models.generate_content(...)
"""
from __future__ import annotations

import os
import time
from typing import Any

TRANSIENT = ("429", "500", "502", "503", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "DEADLINE_EXCEEDED")
RATE_LIMIT_PATTERNS = ("429", "RESOURCE_EXHAUSTED")
NOT_FOUND_PATTERNS = ("404", "NOT_FOUND", "is not found")
DEFAULT_FALLBACK_MODELS = "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-2.5-flash,gemini-2.5-flash-lite"


def is_transient(e: Exception) -> bool:
    return any(t in str(e) for t in TRANSIENT)


def is_not_found(e: Exception) -> bool:
    err_str = str(e)
    if any(p in err_str for p in NOT_FOUND_PATTERNS):
        return True
    code = getattr(e, "code", None)
    if code == 404 or str(code) == "404":
        return True
    status = getattr(e, "status", None)
    if status == "NOT_FOUND":
        return True
    return False


def is_rate_limit(e: Exception) -> bool:
    err_str = str(e)
    if any(t in err_str for t in RATE_LIMIT_PATTERNS):
        return True
    code = getattr(e, "code", None)
    if code == 429 or str(code) == "429":
        return True
    status = getattr(e, "status", None)
    if status == "RESOURCE_EXHAUSTED":
        return True
    return False


def is_daily_quota(e: Exception) -> bool:
    s = str(e).lower()
    return is_rate_limit(e) and ("perday" in s or "per day" in s)


class _Models:
    def __init__(
        self,
        inner: Any,
        fallbacks: list[str],
        delays: list[float],
        rate_limit_delays: list[float] | None = None,
        client: Any = None,
    ):
        self.inner = inner
        self.fallbacks = fallbacks
        self.delays = delays
        self.rate_limit_delays = rate_limit_delays if rate_limit_delays is not None else [20.0, 45.0, 90.0]
        self._client = client

    def generate_content(self, model: str, contents: Any, config: Any = None, **kw: Any) -> Any:
        last: Exception | None = None
        models_to_try = [model] + [f for f in self.fallbacks if f != model]
        daily_exhausted: set[str] = set()
        unavailable: set[str] = set()

        for m in models_to_try:
            max_retries = max(len(self.delays), len(self.rate_limit_delays))
            for attempt in range(max_retries + 1):
                try:
                    return self.inner.generate_content(model=m, contents=contents, config=config, **kw)
                except Exception as e:
                    if is_not_found(e):
                        last = e
                        unavailable.add(m)
                        print(f"[llm] model {m} unavailable, skipping")
                        break

                    if is_daily_quota(e):
                        last = e
                        daily_exhausted.add(m)
                        print(f"[llm] {m} daily quota exhausted, skipping")
                        break

                    if not is_transient(e):
                        raise

                    last = e
                    print(f"[llm] {m} busy (try {attempt + 1}): {str(e)[:60]}")
                    if attempt < max_retries:
                        if is_rate_limit(e):
                            delay = (
                                self.rate_limit_delays[attempt]
                                if attempt < len(self.rate_limit_delays)
                                else self.rate_limit_delays[-1]
                            )
                        else:
                            delay = (
                                self.delays[attempt]
                                if attempt < len(self.delays)
                                else self.delays[-1]
                            )
                        time.sleep(delay)
            else:
                print(f"[llm] giving up on {m}")

        candidate_models = [m for m in models_to_try if m not in unavailable]
        if (candidate_models and all(m in daily_exhausted for m in candidate_models)) or (
            len(daily_exhausted) == len(models_to_try)
        ):
            raise RuntimeError("Gemini daily quota exhausted")

        assert last is not None
        raise last


class RetryingClient:
    def __init__(
        self,
        client: Any,
        fallbacks: list[str] | None = None,
        delays: tuple[float, ...] = (3, 8, 20),
        rate_limit_delays: tuple[float, ...] = (20, 45, 90),
    ):
        if fallbacks is None:
            raw_fallbacks = os.getenv("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
            fallbacks = [m.strip() for m in raw_fallbacks.split(",") if m.strip()]
        self.client = client
        self.models = _Models(client.models, fallbacks, list(delays), list(rate_limit_delays), client=client)


