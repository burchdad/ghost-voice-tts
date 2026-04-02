import torch
import numpy as np
from typing import Optional, Tuple, Dict, List
import logging
from functools import lru_cache
import hashlib
from scipy import signal

from app.core.config import get_settings
from app.services.prosody_intelligence import (
    HeuristicProsodyRefiner,
    ProsodyRefinementInput,
    PhonemeAligner,
    ConversationProsodyAdapter,
    ProsodyLearningStore,
)

logger = logging.getLogger(__name__)
settings = get_settings()


class TTSEngine:
    """
    High-performance TTS engine with voice cloning support.
    Wraps Tortoise TTS and provides batching, caching, and GPU optimization.
    """

    def __init__(self):
        self.device = torch.device(settings.TTS_DEVICE)
        self.model = None
        self.speaker_encoder = None
        self.batch_size = settings.TTS_BATCH_SIZE
        self.sample_rate = settings.AUDIO_SAMPLE_RATE
        self._initialized = False
        self._supports_real_synthesis = False
        self._fallback_policy = settings.TTS_FALLBACK_POLICY

    def initialize(self):
        """Lazy load models - only initialize when needed."""
        if self._initialized:
            return

        logger.info(f"Initializing TTS engine with model={settings.TTS_MODEL} on {self.device}")

        try:
            if settings.TTS_MODEL == "tortoise":
                self._load_tortoise()
            elif settings.TTS_MODEL == "vits":
                self._load_vits()
            else:
                raise ValueError(f"Unsupported TTS model: {settings.TTS_MODEL}")

            if settings.VOICE_CLONE_ENABLED:
                self._load_speaker_encoder()

            self._initialized = True
            logger.info("TTS engine initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize TTS engine: {e}")
            self._initialized = True   # allow fallback even if model load fails

    def _load_tortoise(self):
        """Load Tortoise TTS model."""
        try:
            from tortoise import api as tortoise_api
            self.model = tortoise_api.TextToSpeech()
            self._supports_real_synthesis = True
            logger.info("Tortoise TTS model loaded")
        except Exception:
            logger.warning("Tortoise TTS unavailable — will use fallback synthesis")

    def _load_vits(self):
        """Load VITS model for faster inference."""
        try:
            logger.info("VITS model would be loaded here")
        except ImportError:
            logger.warning("VITS not installed")

    def _load_speaker_encoder(self):
        """Load speaker encoder for voice embeddings."""
        try:
            from resemblyzer import VoiceEncoder
            self.speaker_encoder = VoiceEncoder()
            logger.info("Speaker encoder loaded")
        except Exception as e:
            logger.error(f"Failed to load speaker encoder: {e}")

    def warm_load(self):
        """Warm-load models and keep them in memory for fast inference."""
        if not self._initialized:
            self.initialize()
        try:
            logger.info("Starting model warm-load...")
            audio, sr = self.synthesize("This is a warm-up test.")
            logger.info(
                "Warm-load complete. Generated %d samples at %dHz", len(audio), sr
            )
        except Exception as e:
            logger.warning(f"Warm-load failed (non-blocking): {e}")

    # =========================================================================
    # Core synthesis — with full prosody resolution hierarchy
    # =========================================================================

    def synthesize(
        self,
        text: str,
        voice_embedding: Optional[np.ndarray] = None,
        speed: float = 1.0,
        pitch: float = 1.0,
        style: str = "normal",
        mode: str = "balanced",
        voice_seed: Optional[int] = None,
        # ── Individual emotion fields ─────────────────────────────────────────
        emotion: Optional[str] = None,
        emotion_secondary: Optional[str] = None,
        emotion_blend: float = 0.0,
        emotion_intensity: float = 1.0,
        emotion_curve: str = "arc",
        # ── Single template + optional axis overrides ─────────────────────────
        prosody_template: Optional[str] = None,
        prosody_template_axes: Optional[dict] = None,
        # ── Multi-template composition ────────────────────────────────────────
        template_composition_data: Optional[list] = None,
        # ── Context-aware auto-selection ──────────────────────────────────────
        auto_template: bool = False,
        # ── Hierarchical prosody (word-level emphasis + pauses) ───────────────
        hierarchical_prosody_config: Optional[dict] = None,
        # ── Session-aware adaptation ────────────────────────────────────────────
        session_id: Optional[str] = None,
        ml_refinement: bool = True,
        phoneme_alignment: bool = True,
    ) -> Tuple[np.ndarray, int]:
        """Synthesize speech with deterministic prosody controls.

        Resolution priority (highest wins):
        1. ``template_composition_data`` — multi-template weighted blend
        2. ``prosody_template`` + optional ``prosody_template_axes``
        3. ``auto_template`` — keyword-based OS-layer selection
        4. Explicit ``emotion`` + secondary/blend/intensity/curve fields
        """
        if not self._initialized:
            self.initialize()

        from app.services.emotion_engine import EmotionEngine, TemplateSelector

        # ── Resolve emotion profile and temporal curve ─────────────────────────
        emotion_profile = None
        resolved_curve = emotion_curve
        resolved_template: Optional[str] = prosody_template
        selector_confidence = 0.0
        target_intensity = emotion_intensity

        if template_composition_data:
            # Path 1: multi-template blend → EmotionProfile directly
            try:
                names = [item["name"] for item in template_composition_data]
                weights = [item.get("weight", 1.0) for item in template_composition_data]
                axes_list = [item.get("axes") for item in template_composition_data]
                emotion_profile = EmotionEngine.compose_templates(names, weights, axes_list)
                dominant = max(template_composition_data, key=lambda x: x.get("weight", 1.0))
                try:
                    resolved_curve = EmotionEngine.resolve_template(dominant["name"]).emotion_curve
                except Exception:
                    pass
                resolved_template = dominant["name"]
                emotion = names[0]
                logger.info("Template composition: %s", names)
            except Exception as exc:
                logger.warning("Template composition failed: %s — using neutral", exc)

        elif prosody_template:
            # Path 2: single template + optional axis overrides
            try:
                tmpl = EmotionEngine.resolve_template(prosody_template, **(prosody_template_axes or {}))
                emotion = tmpl.emotion
                emotion_secondary = tmpl.secondary_emotion
                emotion_blend = tmpl.emotion_blend
                emotion_intensity = tmpl.emotion_intensity
                target_intensity = tmpl.emotion_intensity
                resolved_curve = tmpl.emotion_curve
                emotion_profile = EmotionEngine.build_profile(
                    emotion=emotion,
                    secondary_emotion=emotion_secondary,
                    secondary_weight=emotion_blend,
                    intensity=emotion_intensity,
                )
                resolved_template = prosody_template
                logger.info("Template '%s' resolved → emotion=%s", prosody_template, emotion)
            except ValueError as exc:
                logger.warning("Unknown prosody template '%s': %s — ignoring", prosody_template, exc)

        elif auto_template:
            # Path 3: context-aware auto-selection from text keywords
            try:
                selected_name, selector_confidence = TemplateSelector.select(text)
                tmpl = None
                if selected_name != "neutral":
                    tmpl = EmotionEngine.resolve_template(selected_name)
                if tmpl is not None:
                    emotion = tmpl.emotion
                    resolved_curve = tmpl.emotion_curve
                    target_intensity = tmpl.emotion_intensity
                    resolved_template = selected_name
                    emotion_profile = EmotionEngine.build_profile(
                        emotion=tmpl.emotion,
                        secondary_emotion=tmpl.secondary_emotion,
                        secondary_weight=tmpl.emotion_blend,
                        intensity=tmpl.emotion_intensity,
                    )
                    logger.info("Auto-template selected: %s (conf=%.2f)", selected_name, selector_confidence)
            except Exception as exc:
                logger.warning("Auto-template selection failed: %s — ignoring", exc)

        elif emotion:
            # Path 4: explicit emotion fields
            emotion_profile = EmotionEngine.build_profile(
                emotion=emotion,
                secondary_emotion=emotion_secondary,
                secondary_weight=emotion_blend,
                intensity=emotion_intensity,
            )
            resolved_curve = emotion_curve

        # Optional real-time conversation adaptation based on session trends.
        if emotion_profile is not None and session_id:
            adapted_template, adapted_intensity = ConversationProsodyAdapter.update(
                session_id=session_id,
                text=text,
                base_template=resolved_template,
                base_intensity=target_intensity,
            )
            if adapted_template and adapted_template != resolved_template:
                try:
                    adapted = EmotionEngine.resolve_template(adapted_template)
                    resolved_curve = adapted.emotion_curve
                    emotion_profile = EmotionEngine.build_profile(
                        emotion=adapted.emotion,
                        secondary_emotion=adapted.secondary_emotion,
                        secondary_weight=adapted.emotion_blend,
                        intensity=adapted_intensity,
                    )
                    resolved_template = adapted_template
                except Exception as exc:
                    logger.debug("Conversation adaptation fallback: %s", exc)

        # Hybrid refinement: rule prior + refiner output.
        if emotion_profile is not None and ml_refinement:
            try:
                session_bias = ProsodyLearningStore.get_session_bias(session_id)
                # Fallback model provider label used for learning buckets.
                provider_name = settings.TTS_MODEL if self._supports_real_synthesis else "fallback"
                provider_bias = ProsodyLearningStore.get_provider_bias(provider_name)

                refiner = HeuristicProsodyRefiner()
                refined = refiner.refine(
                    ProsodyRefinementInput(
                        text=text,
                        template_name=resolved_template,
                        curve=resolved_curve,
                        intensity=target_intensity,
                        auto_template_used=auto_template,
                        selector_confidence=selector_confidence,
                        session_intensity_delta=float(session_bias.get("intensity_delta", 0.0)),
                        session_pause_scale=float(session_bias.get("pause_scale", 1.0)),
                        provider_quality_bias=float(provider_bias.get("score", 0.0)),
                        historical_quality=float(session_bias.get("historical_quality", 0.5)),
                    )
                )
                # Confidence-gated merge with deterministic prior.
                alpha = max(0.25, min(0.85, 1.0 - refined.confidence))
                merged_intensity = (alpha * target_intensity) + ((1.0 - alpha) * refined.intensity)
                target_intensity = max(0.2, min(2.0, merged_intensity))
                resolved_curve = refined.curve
                emotion_profile = EmotionEngine.build_profile(
                    emotion=emotion or "neutral",
                    secondary_emotion=emotion_secondary,
                    secondary_weight=emotion_blend,
                    intensity=target_intensity,
                )

                if hierarchical_prosody_config is None:
                    hierarchical_prosody_config = {}
                if refined.emphasis_hints:
                    existing = hierarchical_prosody_config.get("word_emphases", [])
                    extra = [
                        {"word_index": idx, "energy_boost": 1.25, "pitch_semitones": 0.75}
                        for idx in refined.emphasis_hints
                    ]
                    hierarchical_prosody_config["word_emphases"] = existing + extra
                hierarchical_prosody_config["pause_scale"] = refined.pause_scale
            except Exception as exc:
                logger.debug("Prosody refinement failed; using deterministic prior: %s", exc)

        # Apply speed + pitch from resolved profile
        if emotion_profile is not None:
            speed = speed * emotion_profile.speed_multiplier
            pitch = pitch * float(2 ** (emotion_profile.pitch_shift / 12.0))

        logger.info(
            "Synthesizing: %s... (len=%d mode=%s seed=%s emotion=%s)",
            text[:50], len(text), mode, voice_seed, emotion or "neutral",
        )

        try:
            if self._supports_real_synthesis and self.model and hasattr(self.model, "tts_with_preset"):
                generated = self.model.tts_with_preset(
                    text=text,
                    voice_samples=None,
                    conditioning_latents=None,
                    preset=(
                        "fast" if mode == "realtime"
                        else ("standard" if mode == "balanced" else "high_quality")
                    ),
                )
                if hasattr(generated, "detach"):
                    generated = generated.detach().cpu().numpy()
                audio = np.asarray(generated, dtype=np.float32).flatten()
                if audio.size == 0:
                    raise RuntimeError("Real synthesis returned empty audio")

                if emotion_profile is not None:
                    audio = self._apply_emotion_processing(
                        audio, emotion or "neutral",
                        profile=emotion_profile, curve=resolved_curve, voice_seed=voice_seed,
                    )
                if hierarchical_prosody_config:
                    if phoneme_alignment:
                        _ = PhonemeAligner.estimate_syllable_spans(text)
                    audio = self._apply_hierarchical_prosody(
                        audio, text,
                        self._build_hierarchical_directive(text, prosody_template or "", hierarchical_prosody_config),
                    )
                return audio, self.sample_rate

            if settings.TTS_REQUIRE_REAL_MODEL:
                raise RuntimeError("Real TTS model required but unavailable")

            if not settings.TTS_ALLOW_SYNTH_FALLBACK or self._fallback_policy == "error":
                raise RuntimeError("Synthesis backend unavailable and fallback is disabled")

            audio, sr = self._synthesize_fallback(text=text, speed=speed, pitch=pitch, voice_seed=voice_seed)

            if emotion_profile is not None:
                audio = self._apply_emotion_processing(
                    audio, emotion or "neutral",
                    profile=emotion_profile, curve=resolved_curve, voice_seed=voice_seed,
                )
            if hierarchical_prosody_config:
                if phoneme_alignment:
                    _ = PhonemeAligner.estimate_syllable_spans(text)
                audio = self._apply_hierarchical_prosody(
                    audio, text,
                    self._build_hierarchical_directive(text, prosody_template or "", hierarchical_prosody_config),
                )
            return audio, sr
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            raise

    # =========================================================================
    # Fallback synthesis (deterministic, no model required)
    # =========================================================================

    def _synthesize_fallback(
        self,
        text: str,
        speed: float = 1.0,
        pitch: float = 1.0,
        voice_seed: Optional[int] = None,
    ) -> Tuple[np.ndarray, int]:
        """Deterministic fallback audio generation based on simple tones."""
        duration_ms = max(200, int(len(text) * (48 / max(speed, 0.5))))
        num_samples = int(self.sample_rate * duration_ms / 1000)
        t = np.linspace(0, duration_ms / 1000, num_samples, endpoint=False)
        base_freq = 180.0 * max(0.5, min(2.0, pitch))
        if voice_seed is not None:
            seed_val = int(voice_seed) % (2 ** 31)
        else:
            seed_val = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
        mod = 0.8 + ((seed_val % 21) / 100.0)
        audio = 0.12 * np.sin(2 * np.pi * base_freq * mod * t)
        return audio.astype(np.float32), self.sample_rate

    # =========================================================================
    # Emotion DSP chain
    # =========================================================================

    def _apply_emotion_processing(
        self,
        audio: np.ndarray,
        emotion: str,
        profile=None,
        curve: str = "arc",
        voice_seed: Optional[int] = None,
    ) -> np.ndarray:
        """Apply emotional DSP using a temporal modulation curve."""
        try:
            from app.services.emotion_engine import EmotionEngine
            if profile is None:
                profile = EmotionEngine.get_emotion_profile(emotion)

            audio = audio.astype(np.float32)
            envelope = self._build_emotion_envelope(len(audio), curve)

            if np.max(np.abs(audio)) > 0:
                energy_curve = 1.0 + ((profile.energy_level - 1.0) * envelope)
                audio = audio * energy_curve
                if np.max(np.abs(audio)) > 1.0:
                    audio = audio / (np.max(np.abs(audio)) + 1e-10)

            if profile.breathiness != 1.0:
                noise_level = max(0.0, 0.005 * (profile.breathiness - 1.0))
                if noise_level > 0:
                    rng = np.random.default_rng(voice_seed)
                    noise = rng.normal(0, noise_level, len(audio))
                    audio = (audio + noise).astype(np.float32)
                    if np.max(np.abs(audio)) > 1.0:
                        audio = audio / (np.max(np.abs(audio)) + 1e-10)

            if profile.tone_sharpness != 1.0:
                audio = self._apply_tone_sharpness(audio, profile.tone_sharpness)
            if profile.prosody_intensity != 1.0:
                audio = self._apply_prosody_modulation(audio, profile.prosody_intensity, curve=curve)
            if profile.tension != 1.0:
                audio = self._apply_tension_effect(audio, profile.tension)

            return audio
        except Exception as e:
            logger.error("Emotion processing failed: %s", e)
            return audio

    def _build_emotion_envelope(self, n: int, curve: str) -> np.ndarray:
        """Build a temporal modulation envelope for a given curve shape."""
        if n <= 1:
            return np.ones(max(1, n), dtype=np.float32)
        x = np.linspace(0.0, 1.0, n, dtype=np.float32)
        if curve == "rise":
            env = x
        elif curve == "fall":
            env = 1.0 - x
        elif curve == "wave":
            env = 0.5 + 0.5 * np.sin(2.0 * np.pi * x)
        elif curve == "arc":
            env = 4.0 * x * (1.0 - x)
        else:   # static
            env = np.ones_like(x)
        # Floor at 0.35 so modulation never fully drops out
        return (0.35 + 0.65 * env).astype(np.float32)

    def _apply_tone_sharpness(self, audio: np.ndarray, sharpness: float) -> np.ndarray:
        """EQ-based tone sharpness filter."""
        if sharpness == 1.0:
            return audio
        try:
            if sharpness > 1.0:
                sos = signal.butter(2, 0.05, "high", output="sos")
                filtered = signal.sosfilt(sos, audio)
                blend = (sharpness - 1.0) * 0.5
            else:
                sos = signal.butter(2, 0.05, "low", output="sos")
                filtered = signal.sosfilt(sos, audio)
                blend = (1.0 - sharpness) * 0.5
            return (audio * (1 - blend) + filtered * blend).astype(np.float32)
        except Exception as e:
            logger.warning("Tone sharpness failed: %s", e)
            return audio

    def _apply_prosody_modulation(
        self, audio: np.ndarray, intensity: float, curve: str = "arc"
    ) -> np.ndarray:
        """Amplitude-modulated prosody variation over time."""
        try:
            if intensity == 1.0:
                return audio
            duration = len(audio) / self.sample_rate
            t = np.linspace(0, duration, len(audio), dtype=np.float32)
            env = self._build_emotion_envelope(len(audio), curve)
            phase = np.sin(2 * np.pi * 3.0 * t)
            depth = 0.02 * (intensity - 1.0)
            return (audio * (1.0 + depth * env * phase)).astype(np.float32)
        except Exception as e:
            logger.warning("Prosody modulation failed: %s", e)
            return audio

    def _apply_tension_effect(self, audio: np.ndarray, tension: float) -> np.ndarray:
        """Tension: tanh saturation (high) or smoothing (low)."""
        try:
            if tension == 1.0:
                return audio
            if tension > 1.0:
                return np.tanh(audio * (1 + (tension - 1.0) * 0.1)).astype(np.float32)
            window = max(2, int(0.01 * self.sample_rate))
            return np.convolve(audio, np.ones(window) / window, mode="same").astype(np.float32)
        except Exception as e:
            logger.warning("Tension effect failed: %s", e)
            return audio

    # =========================================================================
    # Hierarchical prosody
    # =========================================================================

    def _build_hierarchical_directive(
        self,
        text: str,
        template: str,
        config: dict,
    ):
        """Build a HierarchicalProsodyDirective from a serialised config dict."""
        from app.services.emotion_engine import TextAnalyzer, WordEmphasis
        extra_emphases = [
            WordEmphasis(
                word_index=e["word_index"],
                energy_boost=e.get("energy_boost", 1.35),
                pitch_semitones=e.get("pitch_semitones", 1.0),
            )
            for e in config.get("word_emphases", [])
        ]
        return TextAnalyzer.build_directive(
            text=text,
            template=template,
            auto_emphasis=config.get("auto_emphasis", True),
            auto_pauses=config.get("auto_pauses", True),
            pause_scale=config.get("pause_scale", 1.0),
            extra_emphases=extra_emphases,
        )

    def _apply_hierarchical_prosody(
        self,
        audio: np.ndarray,
        text: str,
        directive,
    ) -> np.ndarray:
        """Apply word-level emphasis and pause injections.

        Word timing uses the character-fraction approximation: audio is assumed
        to be uniformly distributed over the text length.  This is an
        intentional approximation — full alignment requires an acoustic model.
        """
        n = len(audio)
        words = text.split()
        n_words = len(words)
        if n_words == 0:
            return audio

        result = audio.astype(np.float32).copy()

        # Word-level energy + rough pitch boost
        for emphasis in (directive.word_emphases or []):
            idx = emphasis.word_index
            if not (0 <= idx < n_words):
                continue
            a = int((idx / n_words) * n)
            b = max(a + 1, int(((idx + 1) / n_words) * n))
            seg = result[a:b] * emphasis.energy_boost
            if emphasis.pitch_semitones > 0 and len(seg) > 10:
                ratio = float(2 ** (emphasis.pitch_semitones / 12.0))
                freq = min(0.49, 0.25 * ratio)
                b_c, a_c = signal.butter(1, freq, btype="high")
                boosted = signal.lfilter(b_c, a_c, seg)
                seg = seg + 0.4 * boosted.astype(np.float32)
            result[a:b] = np.clip(seg, -1.0, 1.0)

        # Pause injections (reverse order to preserve indices)
        for pause in sorted(
            directive.pause_directives or [],
            key=lambda p: p.after_word_index,
            reverse=True,
        ):
            idx = pause.after_word_index
            if not (0 <= idx < n_words):
                continue
            cur_len = len(result)
            insert_at = int(((idx + 1) / n_words) * cur_len)
            silence = np.zeros(int(self.sample_rate * pause.duration_ms / 1000.0), dtype=np.float32)
            result = np.concatenate([result[:insert_at], silence, result[insert_at:]])

        return np.clip(result, -1.0, 1.0)

    # =========================================================================
    # Voice cloning
    # =========================================================================

    def encode_voice(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Encode audio to speaker embedding for voice cloning."""
        if not self.speaker_encoder:
            raise RuntimeError("Speaker encoder not loaded")
        try:
            audio = audio / (np.max(np.abs(audio)) + 1e-10)
            return self.speaker_encoder.embed_utterance(audio)
        except Exception as e:
            logger.error(f"Voice encoding failed: {e}")
            raise

    # =========================================================================
    # SSML + batch synthesis (delegate to core synthesize)
    # =========================================================================

    def synthesize_ssml(
        self,
        ssml_text: str,
        voice_embedding: Optional[np.ndarray] = None,
        language: str = "en",
    ) -> Tuple[np.ndarray, int]:
        """Synthesize SSML with phrase-level control."""
        from app.services.ssml import SSMLParser
        if not self._initialized:
            self.initialize()
        logger.info(f"Synthesizing SSML (len={len(ssml_text)})")
        try:
            parser = SSMLParser()
            segments = parser.parse(ssml_text)
            audio_parts = []
            sr = self.sample_rate
            for segment in segments:
                if not segment.text.strip():
                    continue
                audio, sr = self.synthesize(
                    text=segment.text,
                    voice_embedding=voice_embedding,
                    speed=self._parse_prosody_rate(segment.prosody_rate),
                    pitch=self._parse_prosody_pitch(segment.prosody_pitch),
                )
                audio_parts.append(audio)
                if segment.break_after > 0:
                    silence = np.zeros(int(sr * segment.break_after / 1000), dtype=np.float32)
                    audio_parts.append(silence)
            if audio_parts:
                return np.concatenate(audio_parts), sr
            return np.zeros(self.sample_rate, dtype=np.float32), self.sample_rate
        except Exception as e:
            logger.error(f"SSML synthesis failed: {e}")
            raise

    def synthesize_batch(
        self,
        texts: list,
        voice_embeddings: Optional[list] = None,
        **kwargs,
    ) -> list:
        """Synthesize multiple texts efficiently with batching."""
        results = []
        for i in range(0, len(texts), self.batch_size):
            batch_texts = texts[i:i + self.batch_size]
            batch_embeddings = (voice_embeddings[i:i + self.batch_size] if voice_embeddings else [None] * len(batch_texts))
            logger.info(f"Processing batch {i // self.batch_size + 1} of size {len(batch_texts)}")
            for text, emb in zip(batch_texts, batch_embeddings):
                audio, sr = self.synthesize(text, emb, **kwargs)
                results.append((audio, sr))
        return results

    # =========================================================================
    # Utility helpers
    # =========================================================================

    def _parse_prosody_rate(self, rate_str: Optional[str]) -> float:
        """Parse prosody rate string to multiplier."""
        if not rate_str:
            return 1.0
        rate_map = {"x-slow": 0.6, "slow": 0.8, "medium": 1.0, "fast": 1.2, "x-fast": 1.5}
        if rate_str in rate_map:
            return rate_map[rate_str]
        if "%" in rate_str:
            return float(rate_str.rstrip("%")) / 100
        try:
            return float(rate_str)
        except ValueError:
            return 1.0

    def _parse_prosody_pitch(self, pitch_str: Optional[str]) -> float:
        """Parse prosody pitch string to multiplier."""
        if not pitch_str:
            return 1.0
        pitch_map = {"x-low": 0.5, "low": 0.75, "medium": 1.0, "high": 1.25, "x-high": 1.5}
        if pitch_str in pitch_map:
            return pitch_map[pitch_str]
        if "%" in pitch_str:
            return 1.0 + float(pitch_str.lstrip("+").rstrip("%")) / 100
        if "Hz" in pitch_str:
            return float(pitch_str.rstrip("Hz")) / 100
        try:
            return float(pitch_str)
        except ValueError:
            return 1.0

    def get_cache_key(self, text: str, voice_id: str, **params) -> str:
        """Generate cache key for synthesis result."""
        key_str = f"{text}_{voice_id}_{str(params)}"
        return hashlib.sha256(key_str.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_tts_engine: Optional[TTSEngine] = None


def get_tts_engine() -> TTSEngine:
    """Get or create TTS engine instance."""
    global _tts_engine
    if _tts_engine is None:
        _tts_engine = TTSEngine()
    return _tts_engine
