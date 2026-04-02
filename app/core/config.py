from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )
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
    ENFORCE_SECURE_DEFAULTS: bool = True

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = []
    CORS_ALLOW_METHODS: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    CORS_ALLOW_HEADERS: list[str] = ["Authorization", "Content-Type", "X-API-Key"]

    # TTS capability and fallback policy
    TTS_REQUIRE_REAL_MODEL: bool = False
    TTS_ALLOW_SYNTH_FALLBACK: bool = True
    TTS_FALLBACK_POLICY: str = "sine"  # sine | error
    SYNTHESIS_QUEUE_POLICY: str = "inline-fallback"  # required | inline-fallback
    TTS_WARM_ON_STARTUP: bool = True

    # Startup resilience
    STARTUP_DEGRADED_MODE: bool = True

    # ── Provider routing ──────────────────────────────────────────────────────
    # Which provider to use per latency tier.  Values must be provider slugs
    # known to ProviderRouter: "tortoise" | "vits" | "elevenlabs" | "auto"
    PROVIDER_REALTIME: str = "vits"       # fastest; VITS / ElevenLabs
    PROVIDER_BALANCED: str = "auto"       # auto-route based on health
    PROVIDER_HIGH_QUALITY: str = "tortoise"  # highest quality

    # Provider health monitoring
    PROVIDER_HEALTH_CHECK_INTERVAL: int = 30   # seconds between health sweeps
    PROVIDER_FAILURE_THRESHOLD: int = 3        # failures before mark unhealthy
    PROVIDER_RECOVERY_TIMEOUT: int = 60        # seconds before retry unhealthy provider

    # ── Adaptive provider learning ────────────────────────────────────────────
    # EMA alpha for latency and quality score smoothing (0 < α ≤ 1).
    # Lower = slower adaptation (stable); higher = reacts fast to changes.
    PROVIDER_LEARNING_ALPHA: float = 0.15

    # ── Edge / cloud hybrid deployment ───────────────────────────────────────
    # Deployment profile controls which providers are considered "edge" vs
    # "cloud".  Realtime requests prefer edge providers; high-quality requests
    # prefer cloud providers.  Set DEPLOYMENT_PROFILE to one of:
    #   "cloud"  — all providers run remotely (default)
    #   "edge"   — all providers run locally / on-device
    #   "hybrid" — mix: realtime → edge, high_quality → cloud
    DEPLOYMENT_PROFILE: str = "cloud"       # cloud | edge | hybrid

    # Edge providers — lightweight, fast, low-resource (e.g. VITS local)
    EDGE_PROVIDERS: list = ["vits"]

    # Cloud providers — high-quality, may have higher latency / cost
    CLOUD_PROVIDERS: list = ["tortoise", "elevenlabs"]

    # Latency budget (ms) below which edge is preferred even in "hybrid" mode
    EDGE_LATENCY_BUDGET_MS: int = 400

    # ── Prefetch / streaming tuning ───────────────────────────────────────────
    # How many sentences ahead to pre-synthesise.  0 disables prefetch.
    PREFETCH_HORIZON: int = 2

    # ── Voice session continuity ──────────────────────────────────────────────
    VOICE_SESSION_TTL: int = 1800          # seconds a session lock is kept in Redis
    VOICE_SESSION_PREFIX: str = "vsession"

    # Artifact serving/storage
    PUBLIC_BASE_URL: str = ""
    STATIC_AUDIO_PREFIX: str = "/static/audio"
    
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
    
    # Stripe Billing
    STRIPE_API_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None
    STRIPE_PUBLISHABLE_KEY: Optional[str] = None
    
    # Stripe Pricing (in cents)
    STRIPE_PRICING: dict = {
        "starter": {
            "price_id": "price_starter_test",
            "monthly_cents": 5000,  # $50/month
            "character_limit": 1_000_000,
            "overage_price_per_million_cents": 1500,  # $15 per M chars
        },
        "pro": {
            "price_id": "price_pro_test",
            "monthly_cents": 50000,  # $500/month
            "character_limit": 10_000_000,
            "overage_price_per_million_cents": 1200,  # $12 per M
        },
        "enterprise": {
            "price_id": "price_enterprise_test",
            "monthly_cents": 0,  # Custom
            "character_limit": None,
            "overage_price_per_million_cents": 0,
        },
    }
    
@lru_cache()
def get_settings() -> Settings:
    return Settings()
