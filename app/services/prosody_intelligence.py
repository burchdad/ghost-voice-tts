"""Prosody intelligence layer: hybrid refinement, alignment, evaluation, adaptation.

This module intentionally keeps ML dependencies optional and provides a
production-safe rule+heuristic baseline that can be replaced with a trained
model later without API changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import math
import re
import json
import hashlib


def _ema(prev: float, new_val: float, alpha: float) -> float:
    return (alpha * new_val) + ((1.0 - alpha) * prev)


def _avg(values: List[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


@dataclass
class ProsodyRefinementInput:
    text: str
    template_name: Optional[str] = None
    curve: str = "arc"
    intensity: float = 1.0
    auto_template_used: bool = False
    selector_confidence: float = 0.0
    session_intensity_delta: float = 0.0
    session_pause_scale: float = 1.0
    provider_quality_bias: float = 0.0
    historical_quality: float = 0.5


@dataclass
class ProsodyRefinementOutput:
    template_name: Optional[str]
    curve: str
    intensity: float
    pause_scale: float = 1.0
    confidence: float = 0.0
    emphasis_hints: List[int] = field(default_factory=list)


class ProsodyRefiner:
    """Interface for ML-assisted prosody refinement."""

    def refine(self, features: ProsodyRefinementInput) -> ProsodyRefinementOutput:
        raise NotImplementedError


class HeuristicProsodyRefiner(ProsodyRefiner):
    """Baseline hybrid refiner.

    Behaves like a lightweight re-ranker that nudges the rule prior. This is the
    placeholder for a future trained model with the same interface.
    """

    def refine(self, features: ProsodyRefinementInput) -> ProsodyRefinementOutput:
        text = features.text
        word_count = max(1, len(text.split()))
        exclamations = text.count("!")
        questions = text.count("?")
        comma_count = text.count(",")

        intensity = features.intensity + features.session_intensity_delta
        if exclamations:
            intensity += min(0.35, 0.08 * exclamations)
        if word_count > 35:
            intensity -= 0.10
        intensity += max(-0.2, min(0.2, features.provider_quality_bias * 0.12))
        intensity = max(0.2, min(2.0, intensity))

        curve = features.curve
        if questions >= 2 and curve == "arc":
            curve = "wave"
        if exclamations >= 2 and curve in {"arc", "wave"}:
            curve = "rise"

        pause_scale = features.session_pause_scale + min(0.35, comma_count * 0.03)
        pause_scale = max(0.4, min(2.5, pause_scale))

        tokens = text.split()
        emphasis_hints: List[int] = []
        for i, tok in enumerate(tokens):
            if tok.isupper() and len(tok) >= 3:
                emphasis_hints.append(i)
            if re.search(r"\d", tok) and any(sym in tok for sym in ("$", "%", "EUR", "USD")):
                emphasis_hints.append(i)

        confidence = min(
            1.0,
            0.3
            + (0.4 * features.selector_confidence)
            + min(0.3, (exclamations + questions) * 0.05)
            + max(-0.15, min(0.15, (features.historical_quality - 0.5) * 0.3)),
        )
        return ProsodyRefinementOutput(
            template_name=features.template_name,
            curve=curve,
            intensity=intensity,
            pause_scale=max(0.4, min(2.5, pause_scale)),
            confidence=confidence,
            emphasis_hints=sorted(set(emphasis_hints)),
        )


class ProsodyLearningStore:
    """Persistent online-learning memory for prosody adaptation.

    Stores lightweight EMA aggregates in Redis (with local fallback).
    """

    _LOCAL_SESSION: Dict[str, dict] = {}
    _LOCAL_PROVIDER: Dict[str, dict] = {}

    @classmethod
    def _session_key(cls, session_id: str) -> str:
        return f"prosody:session:{session_id}"

    @classmethod
    def _provider_key(cls, provider_name: str) -> str:
        return f"prosody:provider:{provider_name}"

    @classmethod
    def _mask_session_id(cls, session_id: str) -> str:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
        return f"sess_{digest}"

    @classmethod
    def _decode_json_value(cls, raw: Optional[bytes]) -> Optional[dict]:
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    @classmethod
    def _scan_redis_json(cls, pattern: str, limit: int) -> Dict[str, dict]:
        cache = cls._get_cache()
        if not cache:
            return {}

        entries: Dict[str, dict] = {}
        cursor = 0
        while True:
            cursor, keys = cache.client.scan(cursor, match=pattern, count=min(100, max(1, limit)))
            for key in keys:
                key_str = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                parsed = cls._decode_json_value(cache.get(key_str))
                if parsed is not None:
                    entries[key_str] = parsed
                if len(entries) >= limit:
                    return entries
            if cursor == 0:
                break
        return entries

    @classmethod
    def _get_cache(cls):
        try:
            from app.services.cache import get_redis_cache
            cache = get_redis_cache()
            if cache.health_check():
                return cache
        except Exception:
            pass
        return None

    @classmethod
    def get_session_bias(cls, session_id: Optional[str]) -> dict:
        if not session_id:
            return {
                "intensity_delta": 0.0,
                "pause_scale": 1.0,
                "historical_quality": 0.5,
            }
        cache = cls._get_cache()
        if cache:
            raw = cache.get(cls._session_key(session_id))
            if raw:
                try:
                    return json.loads(raw.decode("utf-8"))
                except Exception:
                    pass
        return cls._LOCAL_SESSION.get(
            session_id,
            {"intensity_delta": 0.0, "pause_scale": 1.0, "historical_quality": 0.5},
        )

    @classmethod
    def get_provider_bias(cls, provider_name: str) -> dict:
        cache = cls._get_cache()
        if cache:
            raw = cache.get(cls._provider_key(provider_name))
            if raw:
                try:
                    return json.loads(raw.decode("utf-8"))
                except Exception:
                    pass
        return cls._LOCAL_PROVIDER.get(provider_name, {"quality": 0.5, "score": 0.0})

    @classmethod
    def record_feedback(
        cls,
        *,
        session_id: Optional[str],
        provider_name: str,
        quality_composite: float,
        naturalness: float,
        emotional_accuracy: float,
        timing_correctness: float,
        alpha: float = 0.2,
    ) -> None:
        cache = cls._get_cache()

        if session_id:
            session_prev = cls.get_session_bias(session_id)
            quality = _ema(float(session_prev.get("historical_quality", 0.5)), quality_composite, alpha)
            # Map quality to small tuning deltas used by the refiner.
            intensity_delta = max(-0.2, min(0.2, (quality - 0.5) * 0.4))
            pause_scale = max(0.7, min(1.4, 1.0 + ((timing_correctness - 0.5) * 0.5)))
            session_new = {
                "historical_quality": quality,
                "intensity_delta": intensity_delta,
                "pause_scale": pause_scale,
            }
            cls._LOCAL_SESSION[session_id] = session_new
            if cache:
                cache.set(cls._session_key(session_id), json.dumps(session_new).encode("utf-8"), ttl=86400)

        provider_prev = cls.get_provider_bias(provider_name)
        provider_quality = _ema(float(provider_prev.get("quality", 0.5)), quality_composite, alpha)
        provider_score = max(-1.0, min(1.0, (provider_quality - 0.5) * 2.0))
        provider_new = {
            "quality": provider_quality,
            "score": provider_score,
            "naturalness": _ema(float(provider_prev.get("naturalness", 0.5)), naturalness, alpha),
            "emotional_accuracy": _ema(float(provider_prev.get("emotional_accuracy", 0.5)), emotional_accuracy, alpha),
            "timing_correctness": _ema(float(provider_prev.get("timing_correctness", 0.5)), timing_correctness, alpha),
        }
        cls._LOCAL_PROVIDER[provider_name] = provider_new
        if cache:
            cache.set(cls._provider_key(provider_name), json.dumps(provider_new).encode("utf-8"), ttl=86400)

    @classmethod
    def get_debug_snapshot(cls, limit: int = 20, include_sessions: bool = True) -> dict:
        """Return a safe, bounded view of learned state for admin observability."""
        limit = max(1, min(200, int(limit)))
        cache = cls._get_cache()

        provider_map: Dict[str, dict] = {}
        session_map: Dict[str, dict] = {}

        if cache:
            provider_map = cls._scan_redis_json("prosody:provider:*", limit)
            if include_sessions:
                session_map = cls._scan_redis_json("prosody:session:*", limit)
        else:
            provider_map = {
                cls._provider_key(name): value
                for name, value in list(cls._LOCAL_PROVIDER.items())[:limit]
            }
            if include_sessions:
                session_map = {
                    cls._session_key(name): value
                    for name, value in list(cls._LOCAL_SESSION.items())[:limit]
                }

        providers = []
        for key, value in sorted(provider_map.items()):
            provider_name = key.split("prosody:provider:", 1)[-1]
            providers.append(
                {
                    "provider": provider_name,
                    "quality": float(value.get("quality", 0.5)),
                    "score": float(value.get("score", 0.0)),
                    "naturalness": float(value.get("naturalness", 0.5)),
                    "emotional_accuracy": float(value.get("emotional_accuracy", 0.5)),
                    "timing_correctness": float(value.get("timing_correctness", 0.5)),
                }
            )

        sessions = []
        if include_sessions:
            for key, value in sorted(session_map.items()):
                session_id = key.split("prosody:session:", 1)[-1]
                sessions.append(
                    {
                        "session_id_mask": cls._mask_session_id(session_id),
                        "historical_quality": float(value.get("historical_quality", 0.5)),
                        "intensity_delta": float(value.get("intensity_delta", 0.0)),
                        "pause_scale": float(value.get("pause_scale", 1.0)),
                    }
                )

        snapshot = {
            "backend": "redis" if cache else "local",
            "limit": limit,
            "provider_count": len(providers),
            "providers": providers,
        }
        if include_sessions:
            snapshot["session_count"] = len(sessions)
            snapshot["sessions"] = sessions
        return snapshot

    @classmethod
    def get_aggregate_snapshot(cls, limit: int = 200) -> dict:
        """Return aggregate-only metrics for strict production observability."""
        limit = max(1, min(500, int(limit)))
        snapshot = cls.get_debug_snapshot(limit=limit, include_sessions=True)

        providers = snapshot.get("providers", [])
        sessions = snapshot.get("sessions", [])

        provider_quality_values = [float(p.get("quality", 0.5)) for p in providers]
        provider_score_values = [float(p.get("score", 0.0)) for p in providers]
        provider_naturalness_values = [float(p.get("naturalness", 0.5)) for p in providers]
        provider_emotion_values = [float(p.get("emotional_accuracy", 0.5)) for p in providers]
        provider_timing_values = [float(p.get("timing_correctness", 0.5)) for p in providers]

        session_quality_values = [float(s.get("historical_quality", 0.5)) for s in sessions]
        session_intensity_values = [float(s.get("intensity_delta", 0.0)) for s in sessions]
        session_pause_values = [float(s.get("pause_scale", 1.0)) for s in sessions]

        return {
            "backend": snapshot.get("backend", "local"),
            "limit": limit,
            "provider_count": len(providers),
            "session_count": len(sessions),
            "provider_metrics": {
                "quality_avg": _avg(provider_quality_values),
                "score_avg": _avg(provider_score_values),
                "naturalness_avg": _avg(provider_naturalness_values),
                "emotional_accuracy_avg": _avg(provider_emotion_values),
                "timing_correctness_avg": _avg(provider_timing_values),
            },
            "session_metrics": {
                "historical_quality_avg": _avg(session_quality_values),
                "intensity_delta_avg": _avg(session_intensity_values),
                "pause_scale_avg": _avg(session_pause_values),
            },
        }


@dataclass
class SyllableSpan:
    word_index: int
    syllable_index: int
    start_char: int
    end_char: int
    stress: float


class PhonemeAligner:
    """Naive grapheme-based aligner placeholder.

    Provides syllable/stress scaffolding that can later be backed by true
    phoneme alignment (G2P + forced alignment) without changing callers.
    """

    _vowels = frozenset("aeiouyAEIOUY")

    @classmethod
    def estimate_syllable_spans(cls, text: str) -> List[SyllableSpan]:
        words = text.split()
        spans: List[SyllableSpan] = []
        cursor = 0
        for w_idx, word in enumerate(words):
            start = text.find(word, cursor)
            if start == -1:
                start = cursor
            end = start + len(word)
            cursor = end

            clusters: List[Tuple[int, int]] = []
            in_vowel = False
            cluster_start = 0
            for i, ch in enumerate(word):
                is_vowel = ch in cls._vowels
                if is_vowel and not in_vowel:
                    cluster_start = i
                    in_vowel = True
                elif not is_vowel and in_vowel:
                    clusters.append((cluster_start, i))
                    in_vowel = False
            if in_vowel:
                clusters.append((cluster_start, len(word)))

            if not clusters:
                clusters = [(0, len(word))]

            for s_idx, (a, b) in enumerate(clusters):
                stress = 1.15 if s_idx == 0 else 1.0
                spans.append(
                    SyllableSpan(
                        word_index=w_idx,
                        syllable_index=s_idx,
                        start_char=start + a,
                        end_char=start + b,
                        stress=stress,
                    )
                )
        return spans


@dataclass
class ProsodyQualityScore:
    naturalness: float
    emotional_accuracy: float
    timing_correctness: float

    @property
    def composite(self) -> float:
        return 0.4 * self.naturalness + 0.35 * self.emotional_accuracy + 0.25 * self.timing_correctness


class ProsodyEvaluator:
    """Lightweight prosody scoring loop for offline/online telemetry."""

    @staticmethod
    def score(
        text: str,
        audio_duration_sec: float,
        expected_curve: str,
        target_emotion_intensity: float,
        detected_pause_count: int = 0,
    ) -> ProsodyQualityScore:
        words = max(1, len(text.split()))
        wps = words / max(audio_duration_sec, 1e-6)

        # Naturalness proxy: speech rate in a broad human range.
        naturalness = max(0.0, 1.0 - abs(wps - 2.7) / 2.7)

        # Emotional accuracy proxy: punctuation + lexical intensity alignment.
        punct_signal = text.count("!") + text.count("?")
        punct_norm = min(1.0, punct_signal / 4.0)
        target_norm = max(0.0, min(1.0, (target_emotion_intensity - 0.5) / 1.5))
        emotional_accuracy = 1.0 - abs(target_norm - punct_norm)

        # Timing correctness proxy: pause ratio and curve-specific cadence hints.
        pause_ratio = detected_pause_count / max(1, words)
        expected_pause = 0.10 if expected_curve in {"arc", "wave"} else 0.06
        timing_correctness = max(0.0, 1.0 - abs(pause_ratio - expected_pause) / 0.25)

        return ProsodyQualityScore(
            naturalness=max(0.0, min(1.0, naturalness)),
            emotional_accuracy=max(0.0, min(1.0, emotional_accuracy)),
            timing_correctness=max(0.0, min(1.0, timing_correctness)),
        )


@dataclass
class ConversationProsodyState:
    session_id: str
    turn_count: int = 0
    urgency_trend: float = 0.0
    sentiment_trend: float = 0.0


class ConversationProsodyAdapter:
    """Real-time adaptation policy using conversation-level signals."""

    _state: Dict[str, ConversationProsodyState] = {}

    @classmethod
    def update(
        cls,
        session_id: Optional[str],
        text: str,
        base_template: Optional[str],
        base_intensity: float,
    ) -> Tuple[Optional[str], float]:
        if not session_id:
            return base_template, base_intensity

        state = cls._state.get(session_id)
        if state is None:
            state = ConversationProsodyState(session_id=session_id)
            cls._state[session_id] = state

        state.turn_count += 1
        urgency_signal = min(1.0, (text.count("!") + text.lower().count("urgent")) / 3.0)
        sentiment_signal = -1.0 if any(w in text.lower() for w in ("sorry", "concern", "issue")) else 0.2

        # Exponential moving updates to reduce jitter.
        state.urgency_trend = 0.75 * state.urgency_trend + 0.25 * urgency_signal
        state.sentiment_trend = 0.8 * state.sentiment_trend + 0.2 * sentiment_signal

        intensity = base_intensity + (0.25 * state.urgency_trend) + (-0.10 * min(0.0, state.sentiment_trend))
        intensity = max(0.2, min(2.0, intensity))

        template = base_template
        if state.urgency_trend > 0.65 and base_template not in {"urgent_alert", "hype_mode"}:
            template = "urgent_alert"
        elif state.sentiment_trend < -0.4 and base_template not in {"empathy_support"}:
            template = "empathy_support"

        return template, intensity
