"""Tests for admin prosody learning debug endpoint behavior."""

import pytest

from app.routes.admin import get_prosody_learning_snapshot, get_prosody_learning_aggregate_snapshot
from app.services.prosody_intelligence import ProsodyLearningStore


@pytest.mark.asyncio
async def test_admin_debug_prosody_learning_masks_session_ids():
    ProsodyLearningStore._LOCAL_SESSION.clear()
    ProsodyLearningStore._LOCAL_PROVIDER.clear()

    raw_session = "session-sensitive-123"
    provider = "unit-provider"

    ProsodyLearningStore.record_feedback(
        session_id=raw_session,
        provider_name=provider,
        quality_composite=0.82,
        naturalness=0.8,
        emotional_accuracy=0.84,
        timing_correctness=0.81,
    )

    data = await get_prosody_learning_snapshot(
        limit=10,
        include_sessions=True,
        admin={"id": "admin-test", "is_admin": True},
    )
    assert "backend" in data
    assert data["provider_count"] >= 1
    assert data["session_count"] >= 1

    provider_names = [entry["provider"] for entry in data["providers"]]
    assert provider in provider_names

    masked_values = [entry["session_id_mask"] for entry in data["sessions"]]
    assert any(mask.startswith("sess_") for mask in masked_values)
    assert raw_session not in str(data)

    ProsodyLearningStore._LOCAL_SESSION.clear()
    ProsodyLearningStore._LOCAL_PROVIDER.clear()


@pytest.mark.asyncio
async def test_admin_debug_prosody_learning_aggregate_is_summary_only():
    ProsodyLearningStore._LOCAL_SESSION.clear()
    ProsodyLearningStore._LOCAL_PROVIDER.clear()

    ProsodyLearningStore.record_feedback(
        session_id="session-1-sensitive",
        provider_name="provider-a",
        quality_composite=0.7,
        naturalness=0.75,
        emotional_accuracy=0.72,
        timing_correctness=0.74,
    )
    ProsodyLearningStore.record_feedback(
        session_id="session-2-sensitive",
        provider_name="provider-b",
        quality_composite=0.9,
        naturalness=0.91,
        emotional_accuracy=0.88,
        timing_correctness=0.9,
    )

    data = await get_prosody_learning_aggregate_snapshot(
        limit=200,
        admin={"id": "admin-test", "is_admin": True},
    )

    assert "backend" in data
    assert data["provider_count"] >= 2
    assert data["session_count"] >= 2
    assert "provider_metrics" in data
    assert "session_metrics" in data
    assert "sessions" not in data
    assert "providers" not in data
    assert "session-1-sensitive" not in str(data)
    assert "session-2-sensitive" not in str(data)

    ProsodyLearningStore._LOCAL_SESSION.clear()
    ProsodyLearningStore._LOCAL_PROVIDER.clear()
