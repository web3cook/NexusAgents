import pytest
from agent.core.retry import retry, rate_limit, TokenBucketRateLimiter, RateLimit
from agent.core.errors import RateLimitError, NetworkError, NexusError

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

def test_retry_rejects_zero_attempts():
    with pytest.raises(ValueError):
        retry(max_attempts=0)

async def test_retry_async_path():
    call_count = 0
    @retry(max_attempts=3, base_delay_seconds=0.01, retryable_on=[NetworkError])
    async def flaky_async():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise NetworkError("timeout")
        return "async-ok"
    result = await flaky_async()
    assert result == "async-ok"
    assert call_count == 2

def test_token_bucket_allows_burst():
    limiter = TokenBucketRateLimiter(RateLimit(calls_per_second=100, burst=5))
    for _ in range(5):
        limiter.acquire("test")  # should not raise

def test_token_bucket_raises_when_empty():
    limiter = TokenBucketRateLimiter(RateLimit(calls_per_second=0.01, burst=1))
    limiter.acquire("test")  # consume the 1 token
    with pytest.raises(RateLimitError):
        limiter.acquire("test")

def test_rate_limit_known_namespace():
    rate_limit("code")  # code has generous limits (50/s, burst=100), should not raise

def test_rate_limit_unknown_namespace():
    with pytest.raises(NexusError, match="Unknown rate-limit namespace"):
        rate_limit("nonexistent")
