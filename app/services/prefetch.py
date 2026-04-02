"""
Predictive prefetch service for Ghost Voice TTS.

How it works
------------
When a streaming request is underway and sentence N is being played to the
caller, the prefetcher fires off synthesis for sentence N+1 *in the
background*.  When the streaming loop eventually reaches N+1, the audio is
ready — removing the ~200–400 ms inter-sentence pause entirely.

Architecture
------------
* ``PrefetchQueue`` — one per active streaming session.  Keeps a small
  asyncio.Queue of ``(index, sentence, Future)`` tuples.
* ``PrefetchManager`` — singleton that maps ``session_id → PrefetchQueue``.
  Called by ``StreamingTTSManager.synthesize_and_stream()`` before starting
  the main loop so background tasks can run concurrently.

Cache strategy
--------------
Prefetched audio is stored in memory (asyncio.Future) *and* in Redis under the
synthesis cache key, so fallback to the normal synthesis path is trivial.

The prefetch horizon (how many sentences ahead to pre-generate) is configurable
via ``PREFETCH_HORIZON`` (default 2).  A horizon of 1 is safest — it means you
only ever pre-generate one sentence ahead.  Set to 0 to disable.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict, NamedTuple, Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class PrefetchEntry(NamedTuple):
    sentence_index: int
    sentence_text: str
    future: "asyncio.Future"
    enqueued_at: float


# ---------------------------------------------------------------------------
# PrefetchQueue  (per-session)
# ---------------------------------------------------------------------------

class PrefetchQueue:
    """
    Manages prefetch for one streaming request.

    Typical lifecycle::

        pq = PrefetchQueue(synthesize_fn)
        pq.schedule(0, sentences[0])  # optional warm first sentence
        pq.schedule(1, sentences[1])  # prefetch look-ahead

        # When streaming loop needs sentence i:
        audio = await pq.get(i, fallback_fn)
    """

    def __init__(
        self,
        synthesize_fn: Callable,
        horizon: int = 2,
    ) -> None:
        """
        Parameters
        ----------
        synthesize_fn:
            Async callable ``async def fn(text) -> (np.ndarray, int)``.
            The prefetcher calls this under a background asyncio.Task.
        horizon:
            Number of sentences ahead to pre-generate.
        """
        self._synthesize = synthesize_fn
        self.horizon = horizon
        self._cache: Dict[int, asyncio.Future] = {}

    def schedule(self, index: int, text: str) -> None:
        """Kick off background synthesis for sentence *index* if not already scheduled."""
        if index in self._cache:
            return
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._cache[index] = future
        asyncio.ensure_future(self._run(index, text, future))

    async def _run(self, index: int, text: str, future: asyncio.Future) -> None:
        try:
            start = time.monotonic()
            result = await self._synthesize(text)
            elapsed = (time.monotonic() - start) * 1000
            logger.debug(
                "Prefetch sentence %d ready in %.1f ms (%d chars)",
                index, elapsed, len(text),
            )
            if not future.done():
                future.set_result(result)
        except Exception as exc:
            logger.warning("Prefetch sentence %d failed: %s", index, exc)
            if not future.done():
                future.set_exception(exc)

    async def get(
        self,
        index: int,
        fallback: Callable,
        timeout: float = 5.0,
    ):
        """
        Return the prefetched audio for sentence *index*.

        If the future isn't ready within *timeout* seconds, falls back to
        calling *fallback* synchronously so streaming never stalls.
        """
        future = self._cache.get(index)
        if future is None:
            # Not scheduled — call fallback directly
            return await fallback()

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Prefetch timeout for sentence %d — falling back to inline synthesis",
                index,
            )
            return await fallback()
        except Exception:
            return await fallback()

    def evict(self, up_to_index: int) -> None:
        """Free memory for all sentences before *up_to_index*."""
        keys = [k for k in self._cache if k < up_to_index]
        for k in keys:
            del self._cache[k]


# ---------------------------------------------------------------------------
# PrefetchManager  (singleton)
# ---------------------------------------------------------------------------

class PrefetchManager:
    """
    Singleton that creates / cleans up ``PrefetchQueue`` objects.
    """

    def __init__(self) -> None:
        self._queues: Dict[str, PrefetchQueue] = {}

    def create(
        self,
        request_id: str,
        synthesize_fn: Callable,
        sentences: list,
        horizon: int = None,
    ) -> PrefetchQueue:
        """
        Create a ``PrefetchQueue`` for *request_id* and immediately schedule
        the first *horizon* sentences.

        Parameters
        ----------
        request_id:
            Unique per-streaming-request identifier (job_id, websocket id, …).
        synthesize_fn:
            ``async fn(text: str) -> (np.ndarray, int)``
        sentences:
            Full ordered list of sentences for this request.
        horizon:
            Look-ahead depth.  Defaults to ``PREFETCH_HORIZON`` config value.
        """
        h = horizon if horizon is not None else getattr(settings, "PREFETCH_HORIZON", 2)
        pq = PrefetchQueue(synthesize_fn=synthesize_fn, horizon=h)
        self._queues[request_id] = pq

        # Pre-warm the first *horizon* sentences.
        for i, s in enumerate(sentences[:h]):
            pq.schedule(i, s)

        return pq

    def get(self, request_id: str) -> Optional[PrefetchQueue]:
        return self._queues.get(request_id)

    def release(self, request_id: str) -> None:
        """Clean up after a streaming request completes."""
        self._queues.pop(request_id, None)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: Optional[PrefetchManager] = None


def get_prefetch_manager() -> PrefetchManager:
    global _manager
    if _manager is None:
        _manager = PrefetchManager()
    return _manager
