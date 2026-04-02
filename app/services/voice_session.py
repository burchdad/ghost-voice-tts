"""
Voice session service — lightweight per-session voice continuity.

A *voice session* binds a ``session_id`` to:
  - ``voice_id``        — the voice the caller pinned
  - ``voice_seed``      — deterministic seed for consistent tone
  - ``mode``            — the latency tier
  - ``style`` / ``speed`` / ``pitch``  — synthesis settings that must stay
                                          consistent across turns

Sessions live in Redis with a configurable TTL.
Callers that supply an unknown (or expired) ``session_id`` get a fresh entry
created automatically, so no explicit "open session" call is required.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Redis key template
_KEY_TMPL = "{prefix}:{session_id}"


class VoiceSessionState:
    """Mutable snapshot of a voice session."""

    __slots__ = (
        "session_id",
        "voice_id",
        "voice_seed",
        "mode",
        "style",
        "speed",
        "pitch",
        "created_at",
        "last_used_at",
        "turn_count",
    )

    def __init__(
        self,
        *,
        session_id: str,
        voice_id: str,
        voice_seed: Optional[int],
        mode: str = "balanced",
        style: str = "normal",
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> None:
        self.session_id = session_id
        self.voice_id = voice_id
        self.voice_seed = voice_seed
        self.mode = mode
        self.style = style
        self.speed = speed
        self.pitch = pitch
        now = time.time()
        self.created_at = now
        self.last_used_at = now
        self.turn_count = 0

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "voice_id": self.voice_id,
            "voice_seed": self.voice_seed,
            "mode": self.mode,
            "style": self.style,
            "speed": self.speed,
            "pitch": self.pitch,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "turn_count": self.turn_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceSessionState":
        obj = cls(
            session_id=data["session_id"],
            voice_id=data["voice_id"],
            voice_seed=data.get("voice_seed"),
            mode=data.get("mode", "balanced"),
            style=data.get("style", "normal"),
            speed=data.get("speed", 1.0),
            pitch=data.get("pitch", 1.0),
        )
        obj.created_at = data.get("created_at", obj.created_at)
        obj.last_used_at = data.get("last_used_at", obj.last_used_at)
        obj.turn_count = data.get("turn_count", 0)
        return obj


class VoiceSessionManager:
    """
    CRUD for voice sessions backed by Redis.

    Usage::

        mgr = VoiceSessionManager(redis_cache)

        # On first request in a session
        state = mgr.get_or_create(
            session_id="abc123",
            voice_id=request.voice_id,
            voice_seed=request.voice_seed,
            mode=request.mode,
        )

        # Override request fields with locked session values
        effective_voice_id = state.voice_id
        effective_seed     = state.voice_seed
    """

    def __init__(self, cache) -> None:
        self._cache = cache

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key(self, session_id: str) -> str:
        return _KEY_TMPL.format(
            prefix=settings.VOICE_SESSION_PREFIX,
            session_id=session_id,
        )

    def _load(self, session_id: str) -> Optional[VoiceSessionState]:
        raw = self._cache.get(self._key(session_id))
        if raw is None:
            return None
        try:
            return VoiceSessionState.from_dict(json.loads(raw))
        except Exception as exc:
            logger.warning("Corrupt voice session %s: %s", session_id, exc)
            return None

    def _save(self, state: VoiceSessionState) -> None:
        self._cache.set(
            self._key(state.session_id),
            json.dumps(state.to_dict()),
            ttl=settings.VOICE_SESSION_TTL,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create(
        self,
        *,
        session_id: Optional[str],
        voice_id: str,
        voice_seed: Optional[int],
        mode: str = "balanced",
        style: str = "normal",
        speed: float = 1.0,
        pitch: float = 1.0,
    ) -> VoiceSessionState:
        """
        Return an existing session (if ``session_id`` is known) or create a new
        one.  When an existing session is found the caller-supplied *voice_id*,
        *voice_seed*, and synthesis settings are **ignored** — the locked values
        from the session are used instead, ensuring voice consistency.
        """
        if session_id:
            existing = self._load(session_id)
            if existing:
                existing.turn_count += 1
                existing.last_used_at = time.time()
                self._save(existing)
                logger.debug(
                    "Voice session %s resumed (turn %d, voice=%s)",
                    session_id, existing.turn_count, existing.voice_id,
                )
                return existing

        # New session
        sid = session_id or str(uuid.uuid4())
        # Assign a deterministic seed if none given
        effective_seed = voice_seed if voice_seed is not None else (
            abs(hash(f"{sid}:{voice_id}")) % (2 ** 31)
        )
        state = VoiceSessionState(
            session_id=sid,
            voice_id=voice_id,
            voice_seed=effective_seed,
            mode=mode,
            style=style,
            speed=speed,
            pitch=pitch,
        )
        self._save(state)
        logger.info(
            "Voice session %s created (voice=%s, seed=%d, mode=%s)",
            sid, voice_id, effective_seed, mode,
        )
        return state

    def get(self, session_id: str) -> Optional[VoiceSessionState]:
        """Return the session or None if unknown/expired."""
        return self._load(session_id)

    def delete(self, session_id: str) -> None:
        """Explicitly expire a session."""
        try:
            self._cache.delete(self._key(session_id))
        except Exception as exc:
            logger.warning("Failed to delete voice session %s: %s", session_id, exc)

    def refresh_ttl(self, session_id: str) -> None:
        """Reset the expiry clock for an active session."""
        state = self._load(session_id)
        if state:
            self._save(state)


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

_manager: Optional[VoiceSessionManager] = None


def get_voice_session_manager() -> VoiceSessionManager:
    global _manager
    if _manager is None:
        from app.services.cache import get_redis_cache
        _manager = VoiceSessionManager(get_redis_cache())
    return _manager
