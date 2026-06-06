from agent.core.cost import compute_cost


def test_opus_cost_nonzero():
    usd = compute_cost(1000, 200, 800, 0, "claude-opus-4-8")
    assert usd > 0


def test_cache_read_cheaper_than_input():
    base   = compute_cost(1000, 0, 0,    0, "claude-opus-4-8")
    cached = compute_cost(0,    0, 1000, 0, "claude-opus-4-8")
    assert cached < base


def test_unknown_model_falls_back():
    usd = compute_cost(1000, 200, 0, 0, "some-unknown-model")
    assert usd >= 0


def test_zero_tokens_is_zero():
    assert compute_cost(0, 0, 0, 0, "claude-sonnet-4-6") == 0.0


def test_haiku_cheaper_than_opus():
    haiku = compute_cost(1000, 200, 0, 0, "claude-haiku-4-5-20251001")
    opus  = compute_cost(1000, 200, 0, 0, "claude-opus-4-8")
    assert haiku < opus
