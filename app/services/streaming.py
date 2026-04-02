import asyncio
import logging
import re
from typing import AsyncGenerator, List
import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
import json

from app.services.tts_engine import get_tts_engine
from app.services.cache import get_redis_cache
from app.services.audio_stitcher import AudioStitcher
from app.services.prefetch import get_prefetch_manager
from app.services.synthesis_batcher import get_synthesis_batcher
from app.core.metrics import MetricsCollector, SynthesisTimer
from app.models.db import Voice, SynthesisJob
from sqlmodel import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentence splitter for progressive chunked streaming
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+')


def _split_sentences(text: str, max_chunk_chars: int = 200) -> List[str]:
    """
    Split *text* into sentence-sized chunks suitable for progressive synthesis.

    Sentences longer than *max_chunk_chars* are further sub-divided at clause
    boundaries (commas / semicolons) so the first audio chunk is delivered
    within ~200–400 ms of the request arriving.
    """
    raw = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    result: List[str] = []
    for sentence in raw:
        if len(sentence) <= max_chunk_chars:
            result.append(sentence)
        else:
            # sub-divide at clause punctuation
            parts = re.split(r'(?<=[,;])\s+', sentence)
            current = ""
            for part in parts:
                if len(current) + len(part) + 1 <= max_chunk_chars:
                    current = (current + " " + part).strip()
                else:
                    if current:
                        result.append(current)
                    current = part
            if current:
                result.append(current)
    return result or [text]


class AudioStreamBuffer:
    """Buffer audio chunks for streaming with configurable chunk size."""

    CHUNK_SIZE = 4096  # Samples per chunk (~93ms at 22050 Hz)

    def __init__(self, audio_array: np.ndarray, sample_rate: int):
        self.audio = audio_array.astype(np.float32)
        self.sample_rate = sample_rate
        self.position = 0
        self.total_samples = len(audio_array)

    async def stream_chunks(self) -> AsyncGenerator[bytes, None]:
        """Stream audio as binary chunks."""
        while self.position < self.total_samples:
            chunk = self.audio[self.position : self.position + self.CHUNK_SIZE]
            self.position += self.CHUNK_SIZE
            yield chunk.tobytes()
            await asyncio.sleep(0)


class StreamingTTSManager:
    """
    Manages streaming TTS requests with real-time audio delivery.

    Supports two strategies:

    * **Progressive chunked** (default) — text is split into sentences before
      synthesis so the *first* audio packet is emitted within ~200–400 ms.
      Subsequent sentences are synthesised concurrently in the background and
      streamed as they complete.

    * **Full-then-stream** (legacy) — full synthesis first, then stream audio
      bytes.  Used as fallback when ``progressive=False``.
    """

    def __init__(self):
        self.engine = get_tts_engine()
        self.cache = get_redis_cache()

    # ------------------------------------------------------------------
    # Progressive chunked streaming  (primary path)
    # ------------------------------------------------------------------

    async def synthesize_and_stream(
        self,
        text: str,
        voice_id: str,
        voice_embedding: np.ndarray = None,
        language: str = "en",
        style: str = "normal",
        speed: float = 1.0,
        pitch: float = 1.0,
        mode: str = "balanced",
        voice_seed: int = None,
        progressive: bool = True,
        stitch_audio: bool = True,
        request_id: str = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Synthesize *text* and stream audio as JSON events.

        Events::

            {"type": "start", "sentence_count": N, ...}
            {"type": "chunk", "sentence_index": i, "data": "<base64>",
             "chunk_index": j, "progress": 0.0-1.0, "sample_rate": N}
            {"type": "complete", "audio_duration": s, "sentences_synthesised": N}
            {"type": "error", "error": "...", "error_type": "..."}

        Parameters
        ----------
        stitch_audio:
            Apply silence-trimming and crossfade blending between sentence
            chunks (default True).
        request_id:
            Optional unique identifier for this stream.  Used to key the
            prefetch queue; auto-generated from ``id(self)`` if omitted.
        """
        import base64
        import uuid as _uuid

        req_id = request_id or str(_uuid.uuid4())

        try:
            sentences = _split_sentences(text) if progressive else [text]

            yield {
                "type": "start",
                "message": "Synthesis started",
                "text_length": len(text),
                "sentence_count": len(sentences),
                "language": language,
                "mode": mode,
                "progressive": progressive,
            }

            total_samples_yielded = 0
            sample_rate = self.engine.sample_rate
            chunk_count = 0
            sentences_done = 0
            stitcher = AudioStitcher(sample_rate=sample_rate) if stitch_audio else None

            # ── Set up predictive prefetching + GPU batching ───────────────
            engine_ref = self.engine
            _ve = voice_embedding
            _sp, _pi, _st, _vs = speed, pitch, style, voice_seed
            batcher = get_synthesis_batcher()

            async def _synth(sentence_text: str):
                # Route through the batcher so concurrent sentences from
                # multiple streams are grouped into one GPU call.
                return await batcher.synthesize(
                    sentence_text,
                    voice_embedding=_ve,
                    speed=_sp,
                    pitch=_pi,
                    style=_st,
                    voice_seed=_vs,
                )

            pfm = get_prefetch_manager()
            pq = pfm.create(
                request_id=req_id,
                synthesize_fn=_synth,
                sentences=sentences,
            )

            for s_idx, sentence in enumerate(sentences):
                await asyncio.sleep(0)

                # Schedule look-ahead for the next sentence
                next_idx = s_idx + 1
                if next_idx < len(sentences):
                    pq.schedule(next_idx, sentences[next_idx])

                async def _fallback_synth(s=sentence):
                    return await _synth(s)

                with SynthesisTimer(model="streamed"):
                    audio_array, sample_rate = await pq.get(
                        s_idx, fallback=_fallback_synth
                    )

                # ── Stitch: silence-trim + crossfade blend ──────────────
                if stitcher is not None:
                    is_last = (s_idx == len(sentences) - 1)
                    stitched = stitcher.process(audio_array)
                    if is_last:
                        tail = stitcher.flush()
                        if tail.size > 0:
                            stitched = np.concatenate([stitched, tail]) if stitched.size > 0 else tail
                    audio_array = stitched

                # Free memory for sentences we've already streamed
                pq.evict(s_idx)

                if audio_array.size == 0:
                    sentences_done += 1
                    continue

                total_samples = len(audio_array)
                pos = 0
                while pos < total_samples:
                    raw = audio_array[pos : pos + AudioStreamBuffer.CHUNK_SIZE]
                    pos += AudioStreamBuffer.CHUNK_SIZE
                    total_samples_yielded += len(raw)
                    progress = min(
                        (s_idx + pos / max(total_samples, 1)) / len(sentences),
                        0.99,
                    )
                    yield {
                        "type": "chunk",
                        "sentence_index": s_idx,
                        "data": base64.b64encode(raw.astype(np.float32).tobytes()).decode(),
                        "chunk_index": chunk_count,
                        "progress": progress,
                        "sample_rate": sample_rate,
                    }
                    chunk_count += 1
                    await asyncio.sleep(0)

                sentences_done += 1

            audio_duration = total_samples_yielded / sample_rate
            yield {
                "type": "complete",
                "message": "Synthesis complete",
                "audio_duration": audio_duration,
                "total_chunks": chunk_count,
                "sentences_synthesised": sentences_done,
                "progress": 1.0,
            }

            MetricsCollector.record_synthesis_complete(
                duration=audio_duration,
                success=True,
                num_characters=len(text),
            )

        except Exception as exc:
            logger.error("Stream synthesis failed: %s", exc, exc_info=True)
            yield {
                "type": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
            MetricsCollector.record_synthesis_failure(error_type=type(exc).__name__)
        finally:
            get_prefetch_manager().release(req_id)


class WebSocketConnectionManager:
    """Manages WebSocket connections for streaming synthesis."""
    
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.streaming_manager = StreamingTTSManager()
    
    async def connect(self, websocket: WebSocket, client_id: str):
        """Register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"WebSocket connected: {client_id}")
    
    async def disconnect(self, client_id: str):
        """Close and remove a WebSocket connection."""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"WebSocket disconnected: {client_id}")
    
    async def handle_synthesis_stream(
        self,
        websocket: WebSocket,
        client_id: str,
        text: str,
        voice_id: str,
        voice_embedding: np.ndarray = None,
        **kwargs,
    ):
        """
        Handle streaming synthesis request over WebSocket.
        
        Client sends:
        {
            "action": "synthesize",
            "text": "...",
            "voice_id": "...",
            "language": "en",
            "style": "normal",
            "speed": 1.0,
            "pitch": 1.0
        }
        
        Server streams back events with audio chunks.
        """
        
        try:
            async for event in self.streaming_manager.synthesize_and_stream(
                text=text,
                voice_id=voice_id,
                voice_embedding=voice_embedding,
                **kwargs,
            ):
                await websocket.send_json(event)
        
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected during streaming: {client_id}")
            await self.disconnect(client_id)
        
        except Exception as e:
            logger.error(f"WebSocket error: {e}", exc_info=True)
            try:
                await websocket.send_json({
                    "type": "error",
                    "error": str(e),
                })
            except:
                pass
            await self.disconnect(client_id)


# Singleton manager
_ws_manager: StreamingTTSManager = None


def get_websocket_manager() -> StreamingTTSManager:
    """Get or create WebSocket manager."""
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = StreamingTTSManager()
    return _ws_manager
