"""Global concurrency control for shared vLLM-Omni backend (multi-user)."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from loguru import logger

_lock = asyncio.Lock()
_semaphore: asyncio.Semaphore | None = None
_active = 0
_waiters = 0


def max_concurrent_turns() -> int:
    return max(1, int(os.environ.get("OMNI_MAX_CONCURRENT_TURNS", "8")))


def turn_queue_timeout_s() -> float:
    return float(os.environ.get("OMNI_TURN_QUEUE_TIMEOUT", "120"))


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max_concurrent_turns())
    return _semaphore


def active_turns() -> int:
    return _active


def waiting_turns() -> int:
    return _waiters


@asynccontextmanager
async def omni_turn_slot(*, session_id: str = ""):
    """Limit simultaneous Omni ASR/speak work across all WebRTC sessions."""
    global _active, _waiters
    sem = _get_semaphore()
    label = session_id[:8] if session_id else "?"

    _waiters += 1
    try:
        logger.debug(
            "Omni turn queue: session={} active={} waiting={} cap={}",
            label,
            _active,
            _waiters,
            max_concurrent_turns(),
        )
        await asyncio.wait_for(sem.acquire(), timeout=turn_queue_timeout_s())
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"Omni busy — all {max_concurrent_turns()} speech slots in use. Try again shortly."
        ) from exc
    finally:
        _waiters = max(0, _waiters - 1)

    _active += 1
    logger.debug(
        "Omni turn start: session={} active={}/{}",
        label,
        _active,
        max_concurrent_turns(),
    )
    try:
        yield
    finally:
        _active = max(0, _active - 1)
        sem.release()
        logger.debug(
            "Omni turn end: session={} active={}/{}",
            label,
            _active,
            max_concurrent_turns(),
        )
