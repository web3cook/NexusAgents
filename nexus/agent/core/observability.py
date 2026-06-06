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


def setup_logging(level: int = logging.INFO) -> None:
    """Attach a stderr handler to the nexus logger. Safe to call multiple times."""
    if logger.handlers and not isinstance(logger.handlers[0], logging.NullHandler):
        return
    logger.handlers.clear()
    try:
        from rich.logging import RichHandler
        handler: logging.Handler = RichHandler(
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    except ImportError:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


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
