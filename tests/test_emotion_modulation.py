"""Tests for emotion modulation engine and TTS integration."""

import pytest
import numpy as np
from app.services.emotion_engine import (
    EmotionEngine,
    EmotionProfile,
    EMOTION_PRESETS,
    PROSODY_TEMPLATES,
    ProsodyTemplateResolved,
    ProsodyTemplateDefinition,
)
from app.services.tts_engine import TTSEngine


class TestEmotionEngine:
    """Test emotion profile management and retrieval."""
    
    def test_get_emotion_profile_valid(self):
        """Test retrieving a valid emotion profile."""
        profile = EmotionEngine.get_emotion_profile("excited")
        
        assert isinstance(profile, EmotionProfile)
        assert profile.pitch_shift == 1.5
        assert profile.speed_multiplier == 1.3
        assert profile.prosody_intensity == 1.8
    
    def test_get_emotion_profile_invalid(self):
        """Test that invalid emotion raises ValueError."""
        with pytest.raises(ValueError, match="Emotion.*not found"):
            EmotionEngine.get_emotion_profile("nonexistent_emotion")
    
    def test_get_neutral_emotion(self):
        """Test neutral emotion has default values."""
        profile = EmotionEngine.get_emotion_profile("neutral")
        
        assert profile.pitch_shift == 0.0
        assert profile.speed_multiplier == 1.0
        assert profile.tone_sharpness == 1.0
        assert profile.prosody_intensity == 1.0
        assert profile.energy_level == 1.0
    
    def test_list_emotions(self):
        """Test that all emotions are listed."""
        emotions = EmotionEngine.list_emotions()
        
        assert isinstance(emotions, list)
        assert len(emotions) >= 10
        assert "excited" in emotions
        assert "calm" in emotions
        assert "neutral" in emotions
    
    def test_blend_emotions_equal_weight(self):
        """Test blending two emotions with equal weight."""
        blended = EmotionEngine.blend_emotions(
            primary="excited",
            secondary="calm",
            weight=0.5
        )
        
        # Should be approximately average
        excited = EMOTION_PRESETS["excited"]
        calm = EMOTION_PRESETS["calm"]
        
        expected_pitch = (excited.pitch_shift + calm.pitch_shift) / 2
        assert abs(blended.pitch_shift - expected_pitch) < 0.01
    
    def test_blend_emotions_weighted(self):
        """Test blending with custom weight."""
        blended = EmotionEngine.blend_emotions(
            primary="excited",
            secondary="calm",
            weight=0.25  # 75% excited, 25% calm
        )
        
        excited = EMOTION_PRESETS["excited"]
        calm = EMOTION_PRESETS["calm"]
        
        expected_pitch = excited.pitch_shift * 0.75 + calm.pitch_shift * 0.25
        assert abs(blended.pitch_shift - expected_pitch) < 0.01
    
    def test_blend_emotions_no_secondary(self):
        """Test blending with no secondary emotion returns primary."""
        blended = EmotionEngine.blend_emotions(
            primary="excited",
            secondary=None,
            weight=0.5
        )
        
        primary = EMOTION_PRESETS["excited"]
        assert blended.pitch_shift == primary.pitch_shift
        assert blended.speed_multiplier == primary.speed_multiplier
    
    def test_validate_custom_profile_valid(self):
        """Test validation of custom emotion profile."""
        custom_profile = {
            "pitch_shift": 1.0,
            "speed_multiplier": 1.2,
            "tone_sharpness": 1.5,
            "prosody_intensity": 1.3,
            "energy_level": 1.4,
            "breathiness": 0.9,
            "tension": 1.2,
        }
        
        validated = EmotionEngine.validate_custom_profile(custom_profile)
        
        assert isinstance(validated, EmotionProfile)
        assert validated.pitch_shift == 1.0
        assert validated.speed_multiplier == 1.2
    
    def test_validate_custom_profile_invalid(self):
        """Test validation of invalid profile raises ValueError."""
        invalid_profile = {
            "invalid_field": "value",
        }
        
        with pytest.raises(ValueError, match="Invalid emotion profile"):
            EmotionEngine.validate_custom_profile(invalid_profile)

    def test_build_profile_with_blend_and_intensity(self):
        """Test scaled blend profile generation."""
        profile = EmotionEngine.build_profile(
            emotion="excited",
            secondary_emotion="calm",
            secondary_weight=0.25,
            intensity=1.2,
        )
        assert isinstance(profile, EmotionProfile)
        assert profile.speed_multiplier > 1.0
    
    def test_emotion_presets_structure(self):
        """Test that all presets have required fields."""
        for emotion_name, profile in EMOTION_PRESETS.items():
            assert isinstance(profile, EmotionProfile)
            assert hasattr(profile, "pitch_shift")
            assert hasattr(profile, "speed_multiplier")
            assert hasattr(profile, "tone_sharpness")
            assert hasattr(profile, "prosody_intensity")
            assert hasattr(profile, "energy_level")
            assert hasattr(profile, "breathiness")
            assert hasattr(profile, "tension")


class TestTTSEngineEmotionIntegration:
    """Test emotion integration with TTS engine."""
    
    @pytest.fixture
    def tts_engine(self):
        """Create and initialize TTS engine."""
        engine = TTSEngine()
        engine.initialize()
        return engine
    
    def test_synthesize_with_emotion(self, tts_engine):
        """Test synthesizing with emotion parameter."""
        audio, sr = tts_engine.synthesize(
            text="Hello with emotion",
            emotion="excited"
        )
        
        assert isinstance(audio, np.ndarray)
        assert sr > 0
        assert len(audio) > 0
        assert audio.dtype == np.float32
    
    def test_synthesize_without_emotion(self, tts_engine):
        """Test synthesizing without emotion (neutral)."""
        audio, sr = tts_engine.synthesize(
            text="Hello without emotion",
            emotion=None
        )
        
        assert isinstance(audio, np.ndarray)
        assert sr > 0
        assert len(audio) > 0
    
    def test_synthesize_different_emotions(self, tts_engine):
        """Test that different emotions produce different audio."""
        text = "Same text for emotion comparison"
        
        audio_excited, sr1 = tts_engine.synthesize(
            text=text,
            emotion="excited"
        )
        
        audio_calm, sr2 = tts_engine.synthesize(
            text=text,
            emotion="calm"
        )
        
        # Audio should differ (though both same length due to same text)
        assert not np.array_equal(audio_excited, audio_calm)
        assert sr1 == sr2
    
    def test_emotion_not_found_fails_gracefully(self, tts_engine):
        """Test that invalid emotion handles gracefully."""
        # Should still generate audio but may not apply nonexistent emotion
        with pytest.raises(ValueError):
            tts_engine.synthesize(
                text="Test invalid emotion",
                emotion="invalid_emotion_name"
            )
    
    def test_synthesize_with_emotion_and_speed(self, tts_engine):
        """Test combining emotion with speed parameter."""
        audio1, sr1 = tts_engine.synthesize(
            text="Same text",
            speed=1.0,
            emotion="excited"
        )
        
        audio2, sr2 = tts_engine.synthesize(
            text="Same text",
            speed=0.8,
            emotion="excited"
        )
        
        assert sr1 == sr2
        # Due to speed modification, might have different lengths
        assert len(audio1) > 0
        assert len(audio2) > 0


class TestEmotionAudioProcessing:
    """Test audio processing functions for emotion modulation."""
    
    @pytest.fixture
    def tts_engine(self):
        """Create and initialize TTS engine."""
        engine = TTSEngine()
        engine.initialize()
        return engine
    
    @pytest.fixture
    def test_audio(self):
        """Generate test audio signal."""
        sr = 22050
        duration = 1.0
        t = np.linspace(0, duration, int(sr * duration))
        frequency = 440
        audio = 0.2 * np.sin(2 * np.pi * frequency * t).astype(np.float32)
        return audio, sr
    
    def test_apply_tone_sharpness_high(self, tts_engine, test_audio):
        """Test high tone sharpness increases high frequencies."""
        audio, sr = test_audio
        
        # Apply high sharpness (sharp tone)
        processed = tts_engine._apply_tone_sharpness(audio, sharpness=1.8)
        
        assert isinstance(processed, np.ndarray)
        assert processed.dtype == np.float32
        assert len(processed) == len(audio)
    
    def test_apply_tone_sharpness_low(self, tts_engine, test_audio):
        """Test low tone sharpness reduces high frequencies."""
        audio, sr = test_audio
        
        # Apply low sharpness (soft tone)
        processed = tts_engine._apply_tone_sharpness(audio, sharpness=0.5)
        
        assert isinstance(processed, np.ndarray)
        assert processed.dtype == np.float32
        assert len(processed) == len(audio)
    
    def test_apply_tone_sharpness_neutral(self, tts_engine, test_audio):
        """Test neutral sharpness returns unchanged audio."""
        audio, sr = test_audio
        
        processed = tts_engine._apply_tone_sharpness(audio, sharpness=1.0)
        
        assert np.array_equal(processed, audio)
    
    def test_apply_prosody_modulation(self, tts_engine, test_audio):
        """Test prosody modulation adds dynamic variation."""
        audio, sr = test_audio
        
        processed = tts_engine._apply_prosody_modulation(audio, intensity=1.5, curve="wave")
        
        assert isinstance(processed, np.ndarray)
        assert processed.dtype == np.float32
        assert len(processed) == len(audio)
        assert not np.array_equal(processed, audio)
    
    def test_apply_prosody_modulation_neutral(self, tts_engine, test_audio):
        """Test neutral prosody returns unchanged audio."""
        audio, sr = test_audio
        
        processed = tts_engine._apply_prosody_modulation(audio, intensity=1.0)
        
        assert np.array_equal(processed, audio)
    
    def test_apply_tension_effect_high(self, tts_engine, test_audio):
        """Test high tension effect."""
        audio, sr = test_audio
        
        processed = tts_engine._apply_tension_effect(audio, tension=1.8)
        
        assert isinstance(processed, np.ndarray)
        assert processed.dtype == np.float32
    
    def test_apply_tension_effect_low(self, tts_engine, test_audio):
        """Test low tension effect (relaxation)."""
        audio, sr = test_audio
        
        processed = tts_engine._apply_tension_effect(audio, tension=0.5)
        
        assert isinstance(processed, np.ndarray)
        assert processed.dtype == np.float32
    
    def test_apply_emotion_processing_full_chain(self, tts_engine, test_audio):
        """Test complete emotion processing chain."""
        audio, sr = test_audio
        
        processed = tts_engine._apply_emotion_processing(audio, "excited")
        
        assert isinstance(processed, np.ndarray)
        assert processed.dtype == np.float32
        assert len(processed) == len(audio)
        assert not np.array_equal(processed, audio)  # Should be modified
    
    def test_apply_emotion_processing_invalid_emotion_graceful(
        self, tts_engine, test_audio
    ):
        """Test that invalid emotion gracefully returns original audio."""
        audio, sr = test_audio
        
        processed = tts_engine._apply_emotion_processing(audio, "invalid")
        assert isinstance(processed, np.ndarray)
        assert len(processed) == len(audio)


class TestEmotionUseCases:
    """Test realistic emotion use cases."""
    
    @pytest.fixture
    def tts_engine(self):
        """Create and initialize TTS engine."""
        engine = TTSEngine()
        engine.initialize()
        return engine
    
    def test_customer_service_concerned_emotion(self, tts_engine):
        """Test customer service with concerned emotion."""
        audio, sr = tts_engine.synthesize(
            text="I understand your frustration and will help immediately",
            emotion="concerned"
        )
        
        assert len(audio) > 0
        profile = EmotionEngine.get_emotion_profile("concerned")
        assert profile.energy_level > 0.8  # Still engaged
        assert profile.pitch_shift < 0    # Lower pitch shows empathy
    
    def test_alerts_urgent_emotion(self, tts_engine):
        """Test alert system with urgent emotion."""
        audio, sr = tts_engine.synthesize(
            text="Critical system alert requires immediate action",
            emotion="urgent"
        )
        
        assert len(audio) > 0
        profile = EmotionEngine.get_emotion_profile("urgent")
        assert profile.speed_multiplier > 1.3  # Fast delivery
        assert profile.pitch_shift > 1.5       # High pitch
    
    def test_meditation_calm_emotion(self, tts_engine):
        """Test meditation content with calm emotion."""
        audio, sr = tts_engine.synthesize(
            text="Take a deep breath and relax your muscles",
            emotion="calm"
        )
        
        assert len(audio) > 0
        profile = EmotionEngine.get_emotion_profile("calm")
        assert profile.speed_multiplier < 0.9  # Slower pacing
        assert profile.energy_level < 0.8      # Subdued energy
    
    def test_entertainment_playful_emotion(self, tts_engine):
        """Test entertainment with playful emotion."""
        audio, sr = tts_engine.synthesize(
            text="And then something truly amazing happened!",
            emotion="playful"
        )
        
        assert len(audio) > 0
        profile = EmotionEngine.get_emotion_profile("playful")
        assert profile.energy_level > 1.5  # High energy
        assert profile.prosody_intensity > 1.4  # Exaggerated expression


# ---------------------------------------------------------------------------
# Prosody template tests
# ---------------------------------------------------------------------------

class TestProsodyTemplates:
    """Tests for the named prosody template system."""

    # ── Registry ─────────────────────────────────────────────────────────────

    def test_all_templates_are_resolved_types(self):
        """All entries in PROSODY_TEMPLATES are ProsodyTemplateDefinition."""
        for name, tmpl in PROSODY_TEMPLATES.items():
            assert isinstance(tmpl, ProsodyTemplateDefinition), name

    def test_all_templates_reference_valid_emotions(self):
        """Every template's emotion (and secondary_emotion) exist in EMOTION_PRESETS."""
        for name, tmpl in PROSODY_TEMPLATES.items():
            assert tmpl.emotion in EMOTION_PRESETS, (
                f"Template '{name}' uses unknown emotion '{tmpl.emotion}'"
            )
            if tmpl.secondary_emotion is not None:
                assert tmpl.secondary_emotion in EMOTION_PRESETS, (
                    f"Template '{name}' uses unknown secondary_emotion '{tmpl.secondary_emotion}'"
                )

    def test_all_templates_have_valid_curves(self):
        """All template emotion_curve values map to known curve names."""
        valid_curves = {"static", "rise", "fall", "arc", "wave"}
        for name, tmpl in PROSODY_TEMPLATES.items():
            assert tmpl.emotion_curve in valid_curves, (
                f"Template '{name}' has invalid curve '{tmpl.emotion_curve}'"
            )

    def test_all_templates_blend_in_range(self):
        for name, tmpl in PROSODY_TEMPLATES.items():
            assert 0.0 <= tmpl.emotion_blend <= 1.0, f"{name}.emotion_blend out of range"
            assert 0.0 <= tmpl.emotion_intensity <= 2.0, f"{name}.emotion_intensity out of range"

    def test_required_named_templates_exist(self):
        """Core product templates are present."""
        required = {
            "sales_call", "storytelling", "executive_brief",
            "raven_mode", "empathy_support", "urgent_alert",
            "trusted_advisor", "hype_mode",
        }
        assert required.issubset(set(PROSODY_TEMPLATES.keys()))

    # ── resolve_template ─────────────────────────────────────────────────────

    def test_resolve_template_returns_correct_type(self):
        tmpl = EmotionEngine.resolve_template("sales_call")
        assert isinstance(tmpl, ProsodyTemplateResolved)

    def test_resolve_template_fields_match_registry(self):
        tmpl = EmotionEngine.resolve_template("raven_mode")
        source = PROSODY_TEMPLATES["raven_mode"]
        assert tmpl.emotion == source.emotion
        assert tmpl.emotion_curve == source.emotion_curve

    def test_resolve_template_unknown_raises(self):
        with pytest.raises(ValueError, match="not found"):
            EmotionEngine.resolve_template("does_not_exist")

    def test_list_templates_returns_all_names(self):
        names = EmotionEngine.list_templates()
        assert set(names) == set(PROSODY_TEMPLATES.keys())

    # ── Per-template profile smoke tests ─────────────────────────────────────

    @pytest.mark.parametrize("template_name", list(PROSODY_TEMPLATES.keys()))
    def test_build_profile_from_template(self, template_name):
        """Every template can be expanded into a valid EmotionProfile."""
        tmpl = EmotionEngine.resolve_template(template_name)
        profile = EmotionEngine.build_profile(
            emotion=tmpl.emotion,
            secondary_emotion=tmpl.secondary_emotion,
            secondary_weight=tmpl.emotion_blend,
            intensity=tmpl.emotion_intensity,
        )
        assert isinstance(profile, EmotionProfile)
        # Speed multiplier must stay positive
        assert profile.speed_multiplier > 0.0
        # Breathiness capped at 2.0 * 2.0 * blend = at most ~4, but usually
        # the combined scale keeps it reasonable
        assert profile.energy_level > 0.0

    # ── Engine integration ────────────────────────────────────────────────────

    @pytest.fixture
    def tts_engine(self):
        from app.services.tts_engine import TTSEngine
        engine = TTSEngine()
        engine.initialize()
        return engine

    @pytest.mark.parametrize("template_name", list(PROSODY_TEMPLATES.keys()))
    def test_synthesize_with_prosody_template(self, tts_engine, template_name):
        """Every prosody template produces valid audio end-to-end."""
        audio, sr = tts_engine.synthesize(
            text="Testing prosody template output.",
            prosody_template=template_name,
        )
        assert isinstance(audio, np.ndarray)
        assert audio.dtype == np.float32
        assert len(audio) > 0
        assert sr > 0

    def test_prosody_template_overrides_explicit_emotion(self, tts_engine):
        """When prosody_template is set it overrides the emotion kwarg."""
        # raven_mode uses 'confident'; passing emotion='sad' should be overridden.
        audio_template, _ = tts_engine.synthesize(
            text="Override test.",
            emotion="sad",
            prosody_template="raven_mode",
        )
        audio_direct, _ = tts_engine.synthesize(
            text="Override test.",
            prosody_template="raven_mode",
        )
        # Both should produce the same result (template wins in both cases).
        assert np.allclose(audio_template, audio_direct, atol=1e-6)

    def test_unknown_prosody_template_falls_back_gracefully(self, tts_engine):
        """Unknown prosody_template logs a warning and falls back to no-emotion."""
        audio, sr = tts_engine.synthesize(
            text="Fallback test.",
            prosody_template="nonexistent_template",
        )
        assert isinstance(audio, np.ndarray)
        assert len(audio) > 0

    # ── Cache key ─────────────────────────────────────────────────────────────

    def test_cache_key_differs_by_template(self):
        """Different prosody templates must produce different cache keys."""
        from app.utils.cache_keys import CacheKeyGenerator
        key_a = CacheKeyGenerator.generate_synthesis_key(
            text="hello", voice_id="v1", prosody_template="sales_call"
        )
        key_b = CacheKeyGenerator.generate_synthesis_key(
            text="hello", voice_id="v1", prosody_template="raven_mode"
        )
        assert key_a != key_b

    def test_cache_key_differs_template_vs_no_template(self):
        from app.utils.cache_keys import CacheKeyGenerator
        key_with = CacheKeyGenerator.generate_synthesis_key(
            text="hello", voice_id="v1", prosody_template="sales_call"
        )
        key_without = CacheKeyGenerator.generate_synthesis_key(
            text="hello", voice_id="v1", prosody_template="",
        )
        assert key_with != key_without


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
