"""Tests for persistent prosody learning store behavior."""

from app.services.prosody_intelligence import ProsodyLearningStore


def test_learning_store_updates_provider_bias():
    provider = "unit-test-provider"
    before = ProsodyLearningStore.get_provider_bias(provider)
    ProsodyLearningStore.record_feedback(
        session_id=None,
        provider_name=provider,
        quality_composite=0.9,
        naturalness=0.95,
        emotional_accuracy=0.9,
        timing_correctness=0.85,
    )
    after = ProsodyLearningStore.get_provider_bias(provider)

    assert after["quality"] >= before.get("quality", 0.0)
    assert -1.0 <= after["score"] <= 1.0


def test_learning_store_updates_session_bias():
    session_id = "unit-test-session"
    before = ProsodyLearningStore.get_session_bias(session_id)
    ProsodyLearningStore.record_feedback(
        session_id=session_id,
        provider_name="fallback",
        quality_composite=0.8,
        naturalness=0.8,
        emotional_accuracy=0.8,
        timing_correctness=0.8,
    )
    after = ProsodyLearningStore.get_session_bias(session_id)

    assert after["historical_quality"] >= before.get("historical_quality", 0.0)
    assert -0.2 <= after["intensity_delta"] <= 0.2
    assert 0.7 <= after["pause_scale"] <= 1.4
