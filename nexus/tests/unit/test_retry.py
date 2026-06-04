import asyncio
import time
import pytest
from unittest.mock import MagicMock
from agent.core.retry import retry, rate_limit, TokenBucketRateLimiter, RateLimit
from agent.core.errors import RateLimitError, NetworkError

def test_retry_succeeds_on_third_attempt():
    call_count = 0
    @retry(max_attempts=3, base_delay_seconds=0.01, retryable_on=[NetworkError])
    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise NetworkError("conn reset")
        return "ok"
    assert flaky() == "ok"
    assert call_count == 3

def test_retry_raises_after_max_attempts():
    @retry(max_attempts=2, base_delay_seconds=0.01, retryable_on=[NetworkError])
    def always_fails():
        raise NetworkError("always")
    with pytest.raises(NetworkError):
        always_fails()

def test_retry_does_not_catch_non_retryable():
    from agent.core.errors import PlanningError
    @retry(max_attempts=3, base_delay_seconds=0.01, retryable_on=[NetworkError])
    def raises_planning():
        raise PlanningError("bad spec")
    with pytest.raises(PlanningError):
        raises_planning()

def test_token_bucket_allows_burst():
    limiter = TokenBucketRateLimiter(RateLimit(calls_per_second=100, burst=5))
    for _ in range(5):
        limiter.acquire("test")  # should not raise

def test_token_bucket_raises_when_empty():
    limiter = TokenBucketRateLimiter(RateLimit(calls_per_second=0.01, burst=1))
    limiter.acquire("test")  # consume the 1 token
    with pytest.raises(RateLimitError):
        limiter.acquire("test")

def test_rate_limit_function():
    # Should not raise for non-rate-limited namespaces
    rate_limit("code")  # code namespace has generous limits
