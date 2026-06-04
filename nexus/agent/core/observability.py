from __future__ import annotations
import asyncio
import functools
import json
import logging
import time
from typing import Callable

from agent.core.state import get_session_id

logger = logging.getLogger("nexus")
logger.addHandler(logging.NullHandler())  # library best-practice: let the app configure handlers


def instrument(namespace: str, tool: str) -> Callable:
    def decorator(fn: Callable) -> Callable:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                start = time.monotonic()
                try:
                    result = await fn(*args, **kwargs)
                    _emit(namespace, tool, start, "ok", None)
                    return result
                except Exception as exc:
                    _emit(namespace, tool, start, "error", str(exc))
                    raise
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                start = time.monotonic()
                try:
                    result = fn(*args, **kwargs)
                    _emit(namespace, tool, start, "ok", None)
                    return result
                except Exception as exc:
                    _emit(namespace, tool, start, "error", str(exc))
                    raise
            return sync_wrapper
    return decorator


def _emit(namespace: str, tool: str, start: float, status: str, error: str | None) -> None:
    logger.info(json.dumps({
        "session_id": get_session_id(),
        "namespace": namespace,
        "tool": f"{namespace}.{tool}",
        "duration_ms": int((time.monotonic() - start) * 1000),
        "status": status,
        "error": error,
    }))
