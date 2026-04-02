import pytest
import base64
import io
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, SQLModel, select
from sqlmodel.pool import StaticPool

from app.main import app
from app.core.database import get_session
from app.models.db import User, Voice
from app.dependencies import get_current_user


@pytest.fixture
def session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(session):
    """Create a test client with dependency override."""
    def get_session_override():
        return session

    def get_current_user_override():
        user = session.exec(select(User).where(User.email == "test@example.com")).first()
        if not user:
            user = User(
                email="test@example.com",
                username="testuser",
                hashed_password="hashed_password",
                api_key="test-api-key-123",
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        return user

    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_current_user] = get_current_user_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def test_user(session: Session):
    """Create a test user."""
    user = User(
        email="test@example.com",
        username="testuser",
        hashed_password="hashed_password",
        api_key="test-api-key-123",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def test_voice(session: Session, test_user: User):
    """Create a test voice."""
    voice = Voice(
        owner_id=test_user.id,
        name="Test Voice",
        description="Test voice for unit testing",
        gender="male",
        accent="American",
        language="en",
        speaker_embedding=b"",
        is_public=True,
        is_verified=True,
    )
    session.add(voice)
    session.commit()
    session.refresh(voice)
    return voice


class TestHealthEndpoint:
    """Test health check endpoint."""
    
    def test_health_check(self, client: TestClient):
        """Test /health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"]
        assert data["database"]
        assert data["redis"]


class TestVoiceEndpoints:
    """Test voice management endpoints."""
    
    def test_create_voice(self, client: TestClient):
        """Test voice creation."""
        response = client.post(
            "/voices/create",
            json={
                "name": "New Voice",
                "description": "A new test voice",
                "gender": "female",
                "accent": "British",
                "language": "en",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Voice"
        assert data["id"]
    
    def test_get_voice(self, client: TestClient, test_voice: Voice):
        """Test getting a voice."""
        response = client.get(f"/voices/{test_voice.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_voice.id
        assert data["name"] == test_voice.name
    
    def test_list_voices(self, client: TestClient, test_voice: Voice):
        """Test listing voices."""
        response = client.get("/voices")
        assert response.status_code == 200
        data = response.json()
        assert "voices" in data
        assert any(v["id"] == test_voice.id for v in data["voices"])


class TestSynthesisEndpoints:
    """Test text-to-speech synthesis endpoints."""
    
    def test_synthesize(self, client: TestClient, test_voice: Voice):
        """Test synthesis request."""
        response = client.post(
            "/synthesize",
            json={
                "text": "Hello, world!",
                "voice_id": test_voice.id,
                "language": "en",
                "style": "normal",
                "speed": 1.0,
                "pitch": 1.0,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"]
        assert data["status"] in {"pending", "completed"}
        assert data["progress"] in {0.0, 1.0}
    
    def test_synthesize_empty_text(self, client: TestClient, test_voice: Voice):
        """Test synthesis with empty text."""
        response = client.post(
            "/synthesize",
            json={
                "text": "",
                "voice_id": test_voice.id,
            },
        )
        assert response.status_code == 422
    
    def test_synthesize_nonexistent_voice(self, client: TestClient):
        """Test synthesis with nonexistent voice."""
        response = client.post(
            "/synthesize",
            json={
                "text": "Hello, world!",
                "voice_id": "nonexistent-voice-id",
            },
        )
        assert response.status_code == 404
    
    def test_get_synthesis_status(self, client: TestClient, test_voice: Voice):
        """Test getting synthesis status."""
        # First create a synthesis job
        create_response = client.post(
            "/synthesize",
            json={
                "text": "Test synthesis",
                "voice_id": test_voice.id,
            },
        )
        job_id = create_response.json()["id"]
        
        # Then get its status
        response = client.get(f"/synthesis/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id


class TestEnhanceEndpoint:
    """Test inline enhancement endpoint."""

    def test_enhance_returns_audio_and_deltas(self, client: TestClient, monkeypatch):
        """Test /enhance returns the expected contract with base64 WAV audio."""
        class _DummyEngine:
            def synthesize(self, **kwargs):
                # 0.1s mono silence at 24kHz
                return np.zeros(2400, dtype=np.float32), 24000

        monkeypatch.setattr("app.main.get_tts_engine", lambda: _DummyEngine())

        response = client.post(
            "/enhance",
            json={
                "text": "Enhance this speech",
                "emotion": "confident",
                "emotion_intensity": 1.1,
                "emotion_curve": "arc",
                "auto_template": True,
                "mode": "balanced",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "audioBase64" in data
        assert "deltas" in data
        assert isinstance(data["audioBase64"], str)
        assert isinstance(data["deltas"], dict)

        wav_bytes = base64.b64decode(data["audioBase64"])
        assert len(wav_bytes) > 44
        assert wav_bytes[:4] == b"RIFF"

        assert "speed_delta" in data["deltas"]
        assert "pitch_delta" in data["deltas"]
        assert "curve_from" in data["deltas"]
        assert "curve_to" in data["deltas"]

    def test_enhance_rejects_empty_text(self, client: TestClient):
        """Test /enhance validation on empty text input."""
        response = client.post(
            "/enhance",
            json={
                "text": "",
            },
        )
        assert response.status_code == 422


class TestEnhanceAudioEndpoint:
    """Test uploaded-audio enhancement endpoint."""

    def test_enhance_audio_changes_output(self, client: TestClient):
        """Test /enhance-audio decodes, transforms, and returns modified WAV."""
        sr = 24000
        t = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False, dtype=np.float32)
        # A non-silent tone makes transform verification meaningful.
        tone = (0.15 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        wav_buf = io.BytesIO()
        sf.write(wav_buf, tone, sr, format="WAV")
        input_bytes = wav_buf.getvalue()

        response = client.post(
            "/enhance-audio",
            files={"file": ("input.wav", input_bytes, "audio/wav")},
            data={
                "speed": "1.15",
                "pitch": "1.10",
                "gain_db": "2.0",
                "compression_amount": "1.25",
                "ensure_audible_change": "true",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["processing_applied"] is True
        assert data["hash_changed"] is True
        assert data["input_size_bytes"] == len(input_bytes)
        assert data["output_size_bytes"] > 44
        assert data["input_hash_sha256"] != data["output_hash_sha256"]

        out_bytes = base64.b64decode(data["audioBase64"])
        assert out_bytes[:4] == b"RIFF"

    def test_enhance_audio_rejects_empty_upload(self, client: TestClient):
        """Test /enhance-audio rejects empty file payloads."""
        response = client.post(
            "/enhance-audio",
            files={"file": ("empty.wav", b"", "audio/wav")},
        )
        assert response.status_code == 400


class TestGhostIntelligencePassEndpoint:
    """Test one-click ghost pass endpoint for both text and audio inputs."""

    def test_ghost_pass_text_generation(self, client: TestClient, monkeypatch):
        """Text form input should synthesize and return text_generation source."""
        class _DummyEngine:
            def synthesize(self, **kwargs):
                return np.zeros(2400, dtype=np.float32), 24000

        monkeypatch.setattr("app.main.get_tts_engine", lambda: _DummyEngine())

        response = client.post(
            "/generate-ghost-intelligence-pass",
            data={
                "text": "Make this sound cinematic",
                "emotion": "confident",
                "emotion_curve": "arc",
                "mode": "balanced",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "text_generation"
        assert data["processing_applied"] is True
        assert "deltas" in data
        assert isinstance(data["audioBase64"], str)

    def test_ghost_pass_audio_refinement(self, client: TestClient):
        """Audio upload should refine and return audio_refinement source."""
        sr = 24000
        t = np.linspace(0, 0.4, int(sr * 0.4), endpoint=False, dtype=np.float32)
        tone = (0.15 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        wav_buf = io.BytesIO()
        sf.write(wav_buf, tone, sr, format="WAV")
        input_bytes = wav_buf.getvalue()

        response = client.post(
            "/ghost-intelligence-pass",
            files={"file": ("sample.wav", input_bytes, "audio/wav")},
            data={
                "speed": "1.15",
                "pitch": "1.10",
                "gain_db": "2.0",
                "compression_amount": "1.25",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "audio_refinement"
        assert data["processing_applied"] is True
        assert data["hash_changed"] is True
        assert data["input_size_bytes"] == len(input_bytes)

    def test_ghost_pass_requires_text_or_file(self, client: TestClient):
        """Missing both text and file should be rejected."""
        response = client.post("/ghost-intelligence-pass", data={})
        assert response.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
