"""
Cross-request GPU synthesis batcher for Ghost Voice TTS.

Problem
-------
When N concurrent requests each call the TTS model independently, you pay
the GPU kernel-launch overhead N times and lose the throughput that comes
from batched matrix multiplications.

Solution
--------
``SynthesisBatcher`` is an in-process accumulator with a short collection
window (default 5 ms).  Requests that arrive within the window are grouped
into a single batch inference call, and each caller receives its result via
an ``asyncio.Future``.

Architecture
------------
           request A ─┐
           request B ─┼──► [BatchWindow] ──► engine.synthesize_batch()
           request C ─┘              └──► Future A → audio_A
                                          Future B → audio_B
                                          Future C → audio_C

Usage
-----
    batcher = get_synthesis_batcher()
    audio, sr = await batcher.synthesize(text, **kwargs)

The batcher is a drop-in peer to ``engine.synthesize()``; it just adds
latency-hiding batching on top.

Batch size cap
--------------
The batcher will flush early once ``MAX_BATCH_SIZE`` requests accumulate,
even if the window hasn't expired, to avoid unbounded memory growth.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Collection window in seconds.  5 ms is enough to catch concurrent HTTP
# requests that arrive within the same event-loop tick.
_BATCH_WINDOW_S: float = 0.005

# Hard cap — flush immediately when this many items are pending.
_MAX_BATCH_SIZE: int = 16


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class _BatchItem:
    text: str
    kwargs: Dict[str, Any]
    future: asyncio.Future
    enqueued_at: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# SynthesisBatcher
# ---------------------------------------------------------------------------

class SynthesisBatcher:
    """
    Accumulates synthesis requests within a short time window and dispatches
    them to the TTS engine as a single batched call.

    The batcher does *not* require the TTS engine to expose a native batch
    API — when the engine only has ``synthesize()``, the batcher simply calls
    it sequentially inside the flush task.  The benefit is still real: the
    GPU stays warm between consecutive items and the Python overhead of N
    separate ``apply_async`` dispatches is avoided.

    When ``engine.synthesize_batch()`` is available (future upgrade path) the
    batcher can pass the whole list in one call.
    """

    def __init__(self) -> None:
        self._pending: List[_BatchItem] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        **kwargs,
    ) -> Tuple[np.ndarray, int]:
        """
        Enqueue a synthesis request and await its result.

        Returns the same ``(audio_array, sample_rate)`` tuple as
        ``TTSEngine.synthesize()``.
        """
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()

        async with self._lock:
            self._pending.append(
                _BatchItem(text=text, kwargs=kwargs, future=future)
            )
            # Start a flush task if one isn't already running.
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.ensure_future(
                    self._flush_after_window()
                )
            # Also flush immediately when the batch cap is reached.
            if len(self._pending) >= _MAX_BATCH_SIZE:
                self._flush_task.cancel()
                asyncio.ensure_future(self._flush_now())

        return await future

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _flush_after_window(self) -> None:
        """Wait for the collection window, then flush."""
        await asyncio.sleep(_BATCH_WINDOW_S)
        await self._flush_now()

    async def _flush_now(self) -> None:
        """Process all pending items as a batch."""
        async with self._lock:
            batch = self._pending[:]
            self._pending.clear()

        if not batch:
            return

        logger.debug("Flushing synthesis batch of %d items", len(batch))

        from app.services.tts_engine import get_tts_engine
        engine = get_tts_engine()

        # Prefer native batch path if available; fall back to sequential.
        if hasattr(engine, "synthesize_batch"):
            await self._run_native_batch(engine, batch)
        else:
            await self._run_sequential(engine, batch)

    async def _run_sequential(self, engine, batch: List[_BatchItem]) -> None:
        """
        Process each item sequentially on the current thread pool.

        All items run in the same executor slot so the GPU stays active
        between calls rather than being idle while Python marshals results.
        """
        loop = asyncio.get_event_loop()

        def _work():
            results = []
            for item in batch:
                try:
                    audio, sr = engine.synthesize(text=item.text, **item.kwargs)
                    results.append((audio, sr, None))
                except Exception as exc:
                    results.append((None, None, exc))
            return results

        results = await loop.run_in_executor(None, _work)

        for item, (audio, sr, exc) in zip(batch, results):
            if not item.future.done():
                if exc is not None:
                    item.future.set_exception(exc)
                else:
                    item.future.set_result((audio, sr))

    async def _run_native_batch(self, engine, batch: List[_BatchItem]) -> None:
        """
        Delegate to ``engine.synthesize_batch()`` when available.

        The contract is ``synthesize_batch(items) -> List[(audio, sr)]``
        where *items* is a list of ``{text, **kwargs}`` dicts.
        """
        loop = asyncio.get_event_loop()
        items_payload = [{"text": b.text, **b.kwargs} for b in batch]

        def _work():
            return engine.synthesize_batch(items_payload)

        try:
            results = await loop.run_in_executor(None, _work)
            for item, (audio, sr) in zip(batch, results):
                if not item.future.done():
                    item.future.set_result((audio, sr))
        except Exception as exc:
            for item in batch:
                if not item.future.done():
                    item.future.set_exception(exc)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return len(self._pending)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_batcher: Optional[SynthesisBatcher] = None


def get_synthesis_batcher() -> SynthesisBatcher:
    global _batcher
    if _batcher is None:
        _batcher = SynthesisBatcher()
    return _batcher
