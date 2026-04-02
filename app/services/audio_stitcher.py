"""
Audio stitching optimizer for progressive sentence-level streaming.

Eliminates the three artifacts that make chunked TTS sound unnatural:

1. **Leading / trailing silence** — TTS models pad silence before the first
   phoneme and after the last.  Between sentences this stacks up: chunk N
   ends with 80 ms of silence and chunk N+1 starts with 80 ms, creating a
   160 ms gap the ear notices.

2. **Tone discontinuity** — pitch / energy envelope jumps at the boundary
   because each sentence was conditioned independently.

3. **Timing gaps** — network jitter adds variable delay between chunks.

The stitcher solves (1) by trimming silence at chunk boundaries, and (2)
by applying a short linear crossfade overlap-add between chunks.  It does
not address (3); that's the client's responsibility.

Usage
-----
    stitcher = AudioStitcher(sample_rate=22050)
    for sentence_audio in raw_sentence_chunks:
        blended = stitcher.process(sentence_audio)
        stream(blended)
    # flush the held-back overlap tail
    tail = stitcher.flush()
    if tail.size:
        stream(tail)
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

# RMS silence threshold (linear, not dB).  Samples below this value are
# considered silence.  Roughly −40 dBFS for a 16-bit-range signal.
_DEFAULT_SILENCE_THRESHOLD: float = 0.01

# Crossfade duration in seconds.  200 ms gives clean blending without
# audible smearing, even at 22 050 Hz.
_DEFAULT_CROSSFADE_S: float = 0.02  # 20 ms

# How far into the waveform we scan for the effective start/end
# (avoids scanning the whole array for very long utterances).
_MAX_SCAN_SAMPLES: int = 4096


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trim_silence(
    audio: np.ndarray,
    threshold: float = _DEFAULT_SILENCE_THRESHOLD,
    trim_leading: bool = True,
    trim_trailing: bool = True,
) -> np.ndarray:
    """
    Remove silence at the head/tail of *audio*.

    Works on the absolute amplitude envelope so it handles both positive-
    and negative-biased waveforms correctly.
    """
    if audio.size == 0:
        return audio

    abs_audio = np.abs(audio)
    above = np.where(abs_audio > threshold)[0]

    if above.size == 0:
        # Entirely silent — return a minimal 1-sample array rather than empty
        # so downstream code never divides by zero.
        return audio[:1]

    start = int(above[0])  if trim_leading  else 0
    end   = int(above[-1]) if trim_trailing else len(audio) - 1

    # Keep at least 1 sample of natural fade-in/out context (±1 sample).
    start = max(0, start - 1)
    end   = min(len(audio) - 1, end + 1)

    return audio[start : end + 1]


def _make_crossfade_window(length: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (fade_out, fade_in) linear ramps of *length* samples each."""
    fade_out = np.linspace(1.0, 0.0, length, dtype=np.float32)
    fade_in  = np.linspace(0.0, 1.0, length, dtype=np.float32)
    return fade_out, fade_in


# ---------------------------------------------------------------------------
# AudioStitcher
# ---------------------------------------------------------------------------

class AudioStitcher:
    """
    Stateful per-stream stitcher.  Create one instance per streaming
    synthesis request; call ``process()`` on each sentence chunk in order.

    Parameters
    ----------
    sample_rate:
        Audio sample rate in Hz.
    silence_threshold:
        Linear amplitude below which samples are considered silent.
    crossfade_seconds:
        Duration of the overlap-add crossfade between consecutive chunks.
    trim_silence:
        Whether to trim leading/trailing silence from each chunk.
    """

    def __init__(
        self,
        sample_rate: int = 22050,
        silence_threshold: float = _DEFAULT_SILENCE_THRESHOLD,
        crossfade_seconds: float = _DEFAULT_CROSSFADE_S,
        trim_silence: bool = True,
    ) -> None:
        self.sample_rate = sample_rate
        self.silence_threshold = silence_threshold
        self.crossfade_len = max(1, int(crossfade_seconds * sample_rate))
        self.trim = trim_silence

        # Tail of the previous chunk held back for blending with the next.
        self._overlap_tail: Optional[np.ndarray] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, chunk: np.ndarray) -> np.ndarray:
        """
        Process a single sentence chunk.

        Steps:
          1. Trim leading silence (and trailing silence for non-final chunks).
          2. Blend the tail of the previous chunk with the head of this one
             using a linear crossfade overlap-add.
          3. Hold back the tail of *this* chunk for blending with the next.

        Returns the audio that is safe to stream right now.
        """
        if chunk.size == 0:
            return chunk

        chunk = chunk.astype(np.float32)

        if self.trim:
            # Trim leading silence always; only trim trailing on subsequent
            # chunks (the first may need its natural onset).
            trim_trailing = self._overlap_tail is not None
            chunk = _trim_silence(
                chunk,
                threshold=self.silence_threshold,
                trim_leading=True,
                trim_trailing=trim_trailing,
            )

        if chunk.size == 0:
            return chunk

        # ── Crossfade blend with previous tail ────────────────────────────
        cf = min(self.crossfade_len, len(chunk))

        if self._overlap_tail is not None and self._overlap_tail.size > 0:
            tail = self._overlap_tail
            tail_len = len(tail)

            # Align crossfade to shortest of tail and current chunk head.
            blend_len = min(cf, tail_len, len(chunk))
            fade_out, fade_in = _make_crossfade_window(blend_len)

            blended_head = (
                tail[-blend_len:] * fade_out + chunk[:blend_len] * fade_in
            )

            # The pre-blend portion of the tail that is already emitted and
            # the post-blend body of this chunk.
            pre_tail = tail[:-blend_len] if tail_len > blend_len else np.array([], dtype=np.float32)
            body = chunk[blend_len:]

            output = np.concatenate([pre_tail, blended_head, body[: -cf if cf < len(body) else None]])
        else:
            # First chunk — nothing to blend, just hold back the tail
            output = chunk[: -cf] if cf < len(chunk) else np.array([], dtype=np.float32)

        # Hold the tail of *this* chunk for next iteration.
        self._overlap_tail = chunk[-cf:].copy() if cf <= len(chunk) else chunk.copy()

        return output if output.size > 0 else np.array([], dtype=np.float32)

    def flush(self) -> np.ndarray:
        """
        Emit any remaining held-back overlap tail.

        Must be called after the final chunk has been processed.
        """
        tail = self._overlap_tail
        self._overlap_tail = None
        if tail is None or tail.size == 0:
            return np.array([], dtype=np.float32)

        if self.trim:
            tail = _trim_silence(
                tail,
                threshold=self.silence_threshold,
                trim_trailing=True,
            )

        return tail

    # ------------------------------------------------------------------
    # Stats helpers (for metrics / debugging)
    # ------------------------------------------------------------------

    @staticmethod
    def rms_db(audio: np.ndarray) -> float:
        """Return RMS level in dBFS (−inf for silence)."""
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        if rms < 1e-9:
            return -float("inf")
        import math
        return 20.0 * math.log10(rms)
