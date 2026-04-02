import pytest
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
