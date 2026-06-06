from __future__ import annotations

# USD per 1M tokens — Anthropic pricing (2026-06).
# Each tuple is (input, output, cache_read, cache_creation).
_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-8": (15.00, 75.00, 1.50, 18.75),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30, 3.75),
    "claude-haiku-4-5-20251001": (0.80, 4.00, 0.08, 1.00),
    "_default": (3.00, 15.00, 0.30, 3.75),
}

_M = 1_000_000


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_creation: int,
    model: str,
) -> float:
    """Computes the USD cost of a single model call.

    Args:
        input_tokens: Number of input tokens billed.
        output_tokens: Number of output tokens billed.
        cache_read: Number of cache-read input tokens billed.
        cache_creation: Number of cache-creation input tokens billed.
        model: The model id; falls back to "_default" pricing if unknown.

    Returns:
        The total cost in USD.
    """
    inp, out, cr, cc = _PRICING.get(model, _PRICING["_default"])
    return (
        input_tokens * inp / _M
        + output_tokens * out / _M
        + cache_read * cr / _M
        + cache_creation * cc / _M
    )
