from pydantic_settings import BaseSettings
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    # API Config
    API_TITLE: str = "Ghost Voice TTS"
    API_VERSION: str = "1.0.0"
    API_DESCRIPTION: str = "High-performance text-to-speech service with voice cloning"
    DEBUG: bool = False
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4
    
    # Database
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/ghost_voice_tts"
    DATABASE_ECHO: bool = False
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MAX_CONNECTIONS: int = 50
    
    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND_URL: str = "redis://localhost:6379/2"
    CELERY_TASK_SERIALIZER: str = "json"
    CELERY_RESULT_SERIALIZER: str = "json"
    
    # TTS Configuration
    TTS_MODEL: str = "tortoise"  # tortoise, vits, fastpitch
    TTS_DEVICE: str = "cuda"  # cuda or cpu
    TTS_BATCH_SIZE: int = 4
    TTS_ENABLE_CACHING: bool = True
    TTS_CACHE_TTL: int = 3600  # 1 hour
    
    # Voice Cloning
    VOICE_CLONE_ENABLED: bool = True
    SPEAKER_ENCODER_MODEL: str = "resemblyzer"
    MIN_VOICE_SAMPLE_DURATION: float = 1.0  # seconds
    MAX_VOICE_SAMPLE_DURATION: float = 30.0  # seconds
    
    # Storage (S3/MinIO)
    S3_ENDPOINT_URL: Optional[str] = None
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None
    S3_BUCKET_NAME: str = "ghost-voice-tts"
    S3_REGION: str = "us-east-1"
    
    # Audio Output
    AUDIO_SAMPLE_RATE: int = 22050
    AUDIO_FORMAT: str = "wav"
    MAX_AUDIO_DURATION: int = 300  # 5 minutes
    
    # API Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_PERIOD: int = 60  # seconds
    
    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    # Feature Flags
    ENABLE_STREAMING: bool = True
    ENABLE_VOICE_CLONING: bool = True
    ENABLE_MULTILINGUAL: bool = True
    
    # Monitoring
    PROMETHEUS_ENABLED: bool = True
    METRICS_PORT: int = 8001
    
    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()
