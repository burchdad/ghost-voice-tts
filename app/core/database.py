from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy.pool import NullPool
from typing import Generator
import logging

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# Create database engine
engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    poolclass=NullPool if settings.TTS_DEVICE == "cuda" else None,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)


def create_db_and_tables():
    """Create all database tables."""
    SQLModel.metadata.create_all(engine)
    logger.info("Database tables created/verified")


def get_session() -> Generator[Session, None, None]:
    """Dependency for getting DB session."""
    with Session(engine) as session:
        yield session
