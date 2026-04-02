"""Tests for hybrid prosody intelligence scaffolding."""

from app.services.prosody_intelligence import (
    HeuristicProsodyRefiner,
    ProsodyRefinementInput,
    PhonemeAligner,
    ProsodyEvaluator,
    ConversationProsodyAdapter,
)


def test_refiner_outputs_confident_adjustments():
    refiner = HeuristicProsodyRefiner()
    result = refiner.refine(
        ProsodyRefinementInput(
            text="URGENT! Buy now and save $50 today!!!",
            template_name="sales_call",
            curve="arc",
            intensity=1.1,
            auto_template_used=True,
            selector_confidence=0.7,
        )
    )
    assert result.confidence > 0.3
    assert result.intensity >= 1.1
    assert result.curve in {"arc", "rise", "wave", "fall", "static"}
    assert isinstance(result.emphasis_hints, list)


def test_phoneme_aligner_returns_syllable_spans():
    spans = PhonemeAligner.estimate_syllable_spans("Hello amazing world")
    assert spans
    assert all(s.end_char > s.start_char for s in spans)
    assert all(s.word_index >= 0 for s in spans)


def test_prosody_evaluator_returns_bounded_scores():
    score = ProsodyEvaluator.score(
        text="This is a calm statement.",
        audio_duration_sec=2.0,
        expected_curve="arc",
        target_emotion_intensity=1.0,
        detected_pause_count=1,
    )
    assert 0.0 <= score.naturalness <= 1.0
    assert 0.0 <= score.emotional_accuracy <= 1.0
    assert 0.0 <= score.timing_correctness <= 1.0
    assert 0.0 <= score.composite <= 1.0


def test_conversation_adapter_modifies_intensity_over_turns():
    session_id = "test-session-adapter"
    template, intensity = ConversationProsodyAdapter.update(
        session_id=session_id,
        text="normal sentence",
        base_template="trusted_advisor",
        base_intensity=1.0,
    )
    assert 0.2 <= intensity <= 2.0

    template2, intensity2 = ConversationProsodyAdapter.update(
        session_id=session_id,
        text="URGENT! critical warning!",
        base_template=template,
        base_intensity=intensity,
    )
    assert 0.2 <= intensity2 <= 2.0
    assert template2 in {"trusted_advisor", "urgent_alert", "empathy_support"}
