from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from typing import Callable

from agent.core.state import get_session_id

logger = logging.getLogger("nexus")
# Library best practice: let the application configure handlers.
logger.addHandler(logging.NullHandler())


def setup_logging(level: int = logging.INFO) -> None:
    """Attaches a stderr handler to the nexus logger.

    Safe to call multiple times; subsequent calls are no-ops once a real
    handler is installed. Prefers a RichHandler when rich is available.

    Args:
        level: The logging level to set on the nexus logger.
    """
    if logger.handlers and not isinstance(
        logger.handlers[0], logging.NullHandler
    ):
        return
    logger.handlers.clear()
    try:
        from rich.logging import RichHandler
        handler: logging.Handler = RichHandler(
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        )
        handler.setFormatter(
            logging.Formatter("%(message)s", datefmt="[%X]")
        )
    except ImportError:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
        ))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def instrument(namespace: str, tool: str) -> Callable:
    """Builds a decorator that logs a tool call's duration and status.

    Works with both synchronous and coroutine functions. Errors are
    logged and re-raised.

    Args:
        namespace: The tool's namespace.
        tool: The tool's name within the namespace.

    Returns:
        A decorator that wraps the target callable with instrumentation.
    """
    def decorator(fn: Callable) -> Callable:
        """Wraps fn with sync or async instrumentation as appropriate."""
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


def _emit(
    namespace: str,
    tool: str,
    start: float,
    status: str,
    error: str | None,
) -> None:
    """Logs a single structured tool-call event as JSON.

    Args:
        namespace: The tool's namespace.
        tool: The tool's name within the namespace.
        start: The monotonic start time of the call.
        status: Either "ok" or "error".
        error: The error message, or None on success.
    """
    logger.info(json.dumps({
        "session_id": get_session_id(),
        "namespace": namespace,
        "tool": f"{namespace}.{tool}",
        "duration_ms": int((time.monotonic() - start) * 1000),
        "status": status,
        "error": error,
    }))
