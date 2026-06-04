from __future__ import annotations
import asyncio
import functools
import threading
import time
from dataclasses import dataclass
from typing import Callable, Type

from agent.core.errors import NexusError, RateLimitError

def retry(
    max_attempts: int = 4,
    # max_attempts must be >= 1; 0 would silently return None on every call
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 30.0,
    backoff_factor: float = 2.0,
    retryable_on: list[Type[Exception]] | None = None,
) -> Callable:
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    _retryable = tuple(retryable_on or [NexusError])

    def decorator(fn: Callable) -> Callable:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                delay = base_delay_seconds
                for attempt in range(max_attempts):
                    try:
                        return await fn(*args, **kwargs)
                    except _retryable as exc:
                        if not getattr(exc, "retryable", True) or attempt == max_attempts - 1:
                            raise
                        await asyncio.sleep(min(delay, max_delay_seconds))
                        delay *= backoff_factor
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                delay = base_delay_seconds
                for attempt in range(max_attempts):
                    try:
                        return fn(*args, **kwargs)
                    except _retryable as exc:
                        if not getattr(exc, "retryable", True) or attempt == max_attempts - 1:
                            raise
                        time.sleep(min(delay, max_delay_seconds))
                        delay *= backoff_factor
            return sync_wrapper

    return decorator


@dataclass
class RateLimit:
    calls_per_second: float
    burst: int


class TokenBucketRateLimiter:
    def __init__(self, rate_limit: RateLimit):
        self._tokens = float(rate_limit.burst)
        self._max_tokens = float(rate_limit.burst)
        self._refill_rate = rate_limit.calls_per_second
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, namespace: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._max_tokens,
                self._tokens + (now - self._last_refill) * self._refill_rate,
            )
            self._last_refill = now
            if self._tokens < 1:
                raise RateLimitError(namespace)
            self._tokens -= 1


_RATE_LIMITS: dict[str, RateLimit] = {
    "aws":      RateLimit(calls_per_second=5,   burst=10),
    "k8s":      RateLimit(calls_per_second=20,  burst=50),
    "alert":    RateLimit(calls_per_second=1,   burst=3),
    "docker":   RateLimit(calls_per_second=2,   burst=5),
    "code":     RateLimit(calls_per_second=50,  burst=100),
    "plan":     RateLimit(calls_per_second=10,  burst=20),
    "test":     RateLimit(calls_per_second=5,   burst=10),
    "subagent": RateLimit(calls_per_second=1,   burst=2),
}

_limiters: dict[str, TokenBucketRateLimiter] = {
    ns: TokenBucketRateLimiter(rl) for ns, rl in _RATE_LIMITS.items()
}

def rate_limit(namespace: str) -> None:
    if namespace not in _limiters:
        raise NexusError(f"Unknown rate-limit namespace: '{namespace}'")
    _limiters[namespace].acquire(namespace)
