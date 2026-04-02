from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header, WebSocket, WebSocketDisconnect, status, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
import logging
from datetime import datetime, timezone
import uuid
import json
import base64
import io
import numpy as np
import time
import os
import pickle
import soundfile as sf
from contextlib import asynccontextmanager
import threading

from app.core.config import get_settings
from app.core.database import create_db_and_tables, get_session, engine
from app.core.logging import setup_logging
from app.core.metrics import metrics_registry, MetricsCollector
from app.schemas.tts import (
    SynthesisRequest, SynthesisResponse,
    VoiceResponse, VoiceCloningRequest, VoiceUpdate,
    HealthResponse, UserResponse, SSMLSynthesisRequest,
    SynthesisModeEnum, EnhanceRequest, EnhanceResponse,
)
from app.models.db import User, Voice, SynthesisJob, Subscription, VoiceSample
from app.services.tts_engine import get_tts_engine
from app.services.cache import get_redis_cache
from app.services.streaming import StreamingTTSManager
from app.services.security import get_security_manager, TokenResponse
from app.services.billing import get_billing_manager
from app.tasks.synthesis import synthesize_text_task, encode_voice_samples_task
from app.core.celery import queue_for_mode
from app.middleware import setup_middlewares
from app.dependencies import get_current_user, get_current_user_optional
from app.routes import admin, analytics

# Setup
setup_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_inline_fallback_synthesis(
    *,
    job_id: str,
    text: str,
    voice: Voice,
    speed: float,
    pitch: float,
    style: str,
    emotion: str | None = None,
    secondary_emotion: str | None = None,
    emotion_blend: float = 0.0,
    emotion_intensity: float = 1.0,
    emotion_curve: str = "arc",
    prosody_template: str | None = None,
    prosody_template_axes: dict | None = None,
    template_composition_data: list | None = None,
    auto_template: bool = False,
    hierarchical_prosody_config: dict | None = None,
    session_id: str | None = None,
    ml_refinement: bool = True,
    phoneme_alignment: bool = True,
):
    """Best-effort background fallback when queue transport is unavailable."""
    try:
        synth_engine = get_tts_engine()
        start = time.time()

        voice_embedding = None
        if voice.speaker_embedding:
            try:
                voice_embedding = pickle.loads(voice.speaker_embedding)
            except Exception:
                voice_embedding = None

        audio, sample_rate = synth_engine.synthesize(
            text=text,
            voice_embedding=voice_embedding,
            speed=speed,
            pitch=pitch,
            style=style,
            emotion=emotion,
            emotion_secondary=secondary_emotion,
            emotion_blend=emotion_blend,
            emotion_intensity=emotion_intensity,
            emotion_curve=emotion_curve,
            prosody_template=prosody_template,
            prosody_template_axes=prosody_template_axes,
            template_composition_data=template_composition_data,
            auto_template=auto_template,
            hierarchical_prosody_config=hierarchical_prosody_config,
            session_id=session_id,
            ml_refinement=ml_refinement,
            phoneme_alignment=phoneme_alignment,
        )

        output_dir = os.path.join("uploads", "audio")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{job_id}.wav")
        sf.write(output_path, audio, sample_rate)

        elapsed_ms = (time.time() - start) * 1000

        with Session(engine) as bg_session:
            job = bg_session.exec(select(SynthesisJob).where(SynthesisJob.id == job_id)).first()
            if not job:
                return

            job.status = "completed"
            job.progress = 1.0
            job.audio_url = f"{settings.STATIC_AUDIO_PREFIX}/{job_id}.wav"
            job.audio_duration = float(len(audio) / sample_rate)
            job.inference_time_ms = elapsed_ms
            job.completed_at = utc_now()
            bg_session.add(job)
            bg_session.commit()
    except Exception as fallback_error:
        logger.error(f"Inline fallback failed for job {job_id}: {fallback_error}")
        try:
            with Session(engine) as bg_session:
                job = bg_session.exec(select(SynthesisJob).where(SynthesisJob.id == job_id)).first()
                if job:
                    job.status = "failed"
                    job.error_message = str(fallback_error)
                    bg_session.add(job)
                    bg_session.commit()
        except Exception as db_error:
            logger.error(f"Failed to update fallback error status for job {job_id}: {db_error}")


def _compute_enhance_deltas(request: EnhanceRequest) -> dict[str, float | str | bool | None]:
    """Compute effective prosody deltas for transparency/debugging."""
    from app.services.emotion_engine import EmotionEngine, TemplateSelector

    base_curve = request.emotion_curve.value
    resolved_curve = base_curve
    resolved_emotion = request.emotion
    resolved_secondary = request.secondary_emotion
    resolved_blend = request.emotion_blend
    resolved_intensity = request.emotion_intensity
    template_used: str | None = None
    auto_template_confidence = 0.0

    if request.prosody_template:
        resolved = EmotionEngine.resolve_template(
            request.prosody_template.value,
            **(request.prosody_template_axes or {}),
        )
        resolved_emotion = resolved.emotion
        resolved_secondary = resolved.secondary_emotion
        resolved_blend = resolved.emotion_blend
        resolved_intensity = resolved.emotion_intensity
        resolved_curve = resolved.emotion_curve
        template_used = request.prosody_template.value
    elif request.auto_template:
        selected_name, confidence = TemplateSelector.select(request.text)
        auto_template_confidence = confidence
        if selected_name != "neutral":
            resolved = EmotionEngine.resolve_template(selected_name)
            resolved_emotion = resolved.emotion
            resolved_secondary = resolved.secondary_emotion
            resolved_blend = resolved.emotion_blend
            resolved_intensity = resolved.emotion_intensity
            resolved_curve = resolved.emotion_curve
            template_used = selected_name

    speed_multiplier = 1.0
    pitch_multiplier = 1.0
    if resolved_emotion:
        profile = EmotionEngine.build_profile(
            emotion=resolved_emotion,
            secondary_emotion=resolved_secondary,
            secondary_weight=resolved_blend,
            intensity=resolved_intensity,
        )
        speed_multiplier = profile.speed_multiplier
        pitch_multiplier = float(2 ** (profile.pitch_shift / 12.0))

    effective_speed = request.speed * speed_multiplier
    effective_pitch = request.pitch * pitch_multiplier

    return {
        "template_used": template_used,
        "emotion_from": request.emotion,
        "emotion_to": resolved_emotion,
        "secondary_emotion_to": resolved_secondary,
        "speed_delta": round(effective_speed - request.speed, 6),
        "pitch_delta": round(effective_pitch - request.pitch, 6),
        "intensity_delta": round(resolved_intensity - request.emotion_intensity, 6),
        "blend_delta": round(resolved_blend - request.emotion_blend, 6),
        "curve_from": base_curve,
        "curve_to": resolved_curve,
        "curve_changed": base_curve != resolved_curve,
        "auto_template_confidence": round(auto_template_confidence, 6),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle setup and teardown."""
    logger.info("Starting Ghost Voice TTS service...")

    if (
        settings.ENFORCE_SECURE_DEFAULTS
        and not settings.DEBUG
        and settings.SECRET_KEY == "your-secret-key-change-in-production"
    ):
        raise RuntimeError("Unsafe SECRET_KEY configured for non-debug environment")

    create_db_and_tables()

    if not settings.DEBUG:
        try:
            engine = get_tts_engine()
            engine.warm_load()
            logger.info("TTS engine warmed up and ready for low-latency inference")
        except Exception as e:
            logger.warning(f"TTS engine warmup failed: {e}")

    yield

    logger.info("Shutting down Ghost Voice TTS service...")
    cache = get_redis_cache()
    cache.close()

# Create FastAPI app
app = FastAPI(
    title=settings.API_TITLE,
    description=settings.API_DESCRIPTION,
    version=settings.API_VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

os.makedirs(os.path.join("uploads", "audio"), exist_ok=True)
app.mount("/static", StaticFiles(directory="uploads"), name="static")

# Add CORS middleware
cors_origins = settings.CORS_ALLOWED_ORIGINS
if settings.DEBUG and not cors_origins:
    cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=bool(cors_origins),
    allow_methods=settings.CORS_ALLOW_METHODS,
    allow_headers=settings.CORS_ALLOW_HEADERS,
)

# Setup security, rate limiting, and observability middleware
setup_middlewares(app)

# Include routers for organized endpoints
app.include_router(admin.router)
app.include_router(analytics.router)


# ============ Health & Status Endpoints ============

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    cache = get_redis_cache()
    engine = get_tts_engine()
    
    # Check if cache is operational
    cache_healthy = cache.health_check()
    
    # Check if TTS model is loaded
    model_loaded = engine._initialized
    
    return HealthResponse(
        status="healthy",
        version=settings.API_VERSION,
        database="connected",
        redis="connected" if cache_healthy else "disconnected",
        tts_model=settings.TTS_MODEL,
        model_loaded=model_loaded,
        cache_enabled=cache_healthy,
        timestamp=utc_now(),
    )


@app.get("/tts/capabilities")
async def tts_capabilities():
    """
    Capability descriptor for this TTS node.

    Designed for plug-and-play with Ghost Voice OS and other orchestrators:
    the caller can inspect this response to decide which TTS instance to route
    requests to based on latency tier, language support, streaming capability,
    etc.
    """
    engine = get_tts_engine()
    from app.services.provider_router import get_provider_router
    router = get_provider_router()

    return {
        # ── Core capabilities ──────────────────────────────────────────────
        "supports_streaming": settings.ENABLE_STREAMING,
        "supports_voice_cloning": settings.ENABLE_VOICE_CLONING,
        "supports_ssml": True,
        "supports_multilingual": settings.ENABLE_MULTILINGUAL,
        "supports_progressive_streaming": True,
        # ── Latency tiers ─────────────────────────────────────────────────
        "latency_tiers": {
            "realtime":     {"target_ms": 300,  "provider": settings.PROVIDER_REALTIME},
            "balanced":     {"target_ms": 1500, "provider": settings.PROVIDER_BALANCED},
            "high_quality": {"target_ms": 8000, "provider": settings.PROVIDER_HIGH_QUALITY},
        },
        # ── Provider health ────────────────────────────────────────────────
        "providers": router.health_snapshot(),
        # ── Voices & languages ────────────────────────────────────────────
        "languages": ["en", "es", "fr", "de", "it", "pt", "ja", "ko", "zh", "ru"],
        "models": [settings.TTS_MODEL],
        # ── Session / consistency ─────────────────────────────────────────
        "supports_session_continuity": True,
        "supports_voice_seed": True,
        "voice_session_ttl_seconds": settings.VOICE_SESSION_TTL,
        # ── Runtime flags ─────────────────────────────────────────────────
        "model_loaded": engine._initialized,
        "real_synthesis_available": engine._supports_real_synthesis,
        "fallback_allowed": settings.TTS_ALLOW_SYNTH_FALLBACK,
        "fallback_policy": engine._fallback_policy,
        "api_version": settings.API_VERSION,
          # ── Deployment profile ────────────────────────────────────────────
          "deployment": router.deployment_info(),
     }


@app.get("/tts/providers/health")
async def provider_health():
    """Live health status of all registered TTS providers."""
    from app.services.provider_router import get_provider_router
    router = get_provider_router()
    return {
        "providers": router.health_snapshot(),
        "timestamp": utc_now(),
    }


@app.get("/tts/sessions/{session_id}")
async def get_voice_session(
    session_id: str,
    user: User = Depends(get_current_user),
):
    """Retrieve the locked voice settings for a TTS session."""
    from app.services.voice_session import get_voice_session_manager
    state = get_voice_session_manager().get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return state.to_dict()


@app.delete("/tts/sessions/{session_id}", status_code=204)
async def delete_voice_session(
    session_id: str,
    user: User = Depends(get_current_user),
):
    """Explicitly expire a TTS voice session."""
    from app.services.voice_session import get_voice_session_manager
    get_voice_session_manager().delete(session_id)


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint (simplified)."""
    cache = get_redis_cache()
    
    return {
        "total_syntheses": cache.get_counter("syntheses:total"),
        "total_characters": cache.get_counter("characters:total"),
        "active_jobs": cache.get_counter("jobs:active"),
        "timestamp": utc_now(),
    }


@app.get("/prometheus/metrics")
async def prometheus_metrics():
    """Prometheus metrics in text format."""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    
    return Response(
        generate_latest(metrics_registry),
        media_type=CONTENT_TYPE_LATEST,
    )


# ============ User Endpoints ============

@app.post("/auth/register", response_model=TokenResponse)
async def register_user(
    username: str,
    email: str,
    password: str,
    session: Session = Depends(get_session),
):
    """Register a new user."""
    # Check if user exists
    existing = session.exec(
        select(User).where((User.email == email) | (User.username == username))
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists",
        )
    
    security_mgr = get_security_manager()
    hashed_pwd = security_mgr.hash_password(password)
    
    user = User(
        email=email,
        username=username,
        hashed_password=hashed_pwd,
        tier="free",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    
    # Return JWT token
    token_response = security_mgr.create_access_token(
        user_id=user.id,
        email=user.email,
        tier=user.tier,
    )
    return token_response


@app.post("/auth/login", response_model=TokenResponse)
async def login_user(
    email: str,
    password: str,
    session: Session = Depends(get_session),
):
    """Authenticate user and return JWT token."""
    user = session.exec(select(User).where(User.email == email)).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    
    security_mgr = get_security_manager()
    if not security_mgr.verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    
    # Return JWT token
    token_response = security_mgr.create_access_token(
        user_id=user.id,
        email=user.email,
        tier=user.tier,
    )
    return token_response


@app.post("/auth/refresh", response_model=TokenResponse)
async def refresh_token(
    user: User = Depends(get_current_user),
):
    """Refresh JWT token."""
    security_mgr = get_security_manager()
    token_response = security_mgr.create_access_token(
        user_id=user.id,
        email=user.email,
        tier=user.tier,
    )
    return token_response


@app.get("/me", response_model=UserResponse)
async def get_current_user_info(
    user: User = Depends(get_current_user),
):
    """Get current user information."""
    return user


@app.post("/auth/api-keys/generate")
async def generate_api_key(
    label: str = "",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Generate a new API key for the user."""
    security_mgr = get_security_manager()
    raw_key, hashed_key = security_mgr.generate_api_key(label)
    
    # Save to database
    from app.models.db import APIKeyModel
    api_key = APIKeyModel(
        user_id=user.id,
        hashed_key=hashed_key,
        label=label,
    )
    session.add(api_key)
    session.commit()
    
    return {
        "api_key": raw_key,
        "label": label,
        "note": "Save this key somewhere safe. You won't be able to see it again!",
    }


@app.get("/me/quota")
async def get_my_quota(
    user: User = Depends(get_current_user),
):
    """Get current user's quota information."""
    
    from app.services.quota import get_quota_manager
    quota_mgr = get_quota_manager()
    quota_info = quota_mgr.get_quota_info(user)
    
    return {
        "user_id": user.id,
        "is_premium": user.is_premium,
        **quota_info,
    }


@app.post("/quota/check")
async def check_quota(
    text_length: int,
    user: User = Depends(get_current_user),
):
    """
    Check if user can synthesize text of given length.
    
    Useful for client-side pre-checks before synthesis.
    """
    
    if text_length <= 0 or text_length > 5000:
        raise HTTPException(status_code=400, detail="Invalid text length")
    
    from app.services.quota import get_quota_manager
    quota_mgr = get_quota_manager()
    can_proceed, remaining = quota_mgr.check_monthly_quota(user, text_length)
    
    return {
        "can_synthesize": can_proceed,
        "requested_characters": text_length,
        "remaining_quota": remaining,
        "quota_info": quota_mgr.get_quota_info(user),
    }


# ============ Synthesis Endpoints ============

@app.post("/synthesize", response_model=SynthesisResponse)
async def synthesize(
    request: SynthesisRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Synthesize text to speech.
    
    This endpoint accepts text and a voice ID, then returns a synthesis job.
    Use /synthesis/{job_id} to poll for results.
    """
    
    # Validate text length
    if len(request.text) > 5000:
        raise HTTPException(status_code=400, detail="Text too long (max 5000 chars)")
    
    if len(request.text) < 1:
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    
    # Verify voice exists
    voice = session.exec(select(Voice).where(Voice.id == request.voice_id)).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Check quota
    from app.services.quota import get_quota_manager
    quota_mgr = get_quota_manager()
    can_proceed, remaining = quota_mgr.check_monthly_quota(user, len(request.text))
    
    if not can_proceed:
        raise HTTPException(
            status_code=429,
            detail=f"Quota exceeded. Remaining: {remaining} characters for this month"
        )
    
    # Create synthesis job
    job = SynthesisJob(
        user_id=user.id,
        voice_id=request.voice_id,
        text=request.text,
        text_hash=str(hash(request.text)),  # Simple hash for caching
        language=request.language,
        style=request.style,
        speed=request.speed,
        pitch=request.pitch,
        status="pending",
    )

    session.add(job)
    session.commit()
    session.refresh(job)

    # ── Voice session continuity ──────────────────────────────────────────
    from app.services.voice_session import get_voice_session_manager
    vs_mgr = get_voice_session_manager()
    voice_state = vs_mgr.get_or_create(
        session_id=request.session_id,
        voice_id=request.voice_id,
        voice_seed=request.voice_seed,
        mode=request.mode.value,
        style=request.style.value if hasattr(request.style, "value") else request.style,
        speed=request.speed,
        pitch=request.pitch,
    )
    effective_voice_id = voice_state.voice_id
    effective_seed = voice_state.voice_seed
    effective_session_id = voice_state.session_id
    
    # Deduct quota
    quota_mgr.deduct_quota(user, len(request.text), session)
    
    # Record usage for billing
    billing_mgr = get_billing_manager()
    billing_mgr.record_usage(
        user=user,
        quantity=len(request.text),
        session=session,
        description=f"Synthesis: {request.text[:50]}...",
        synthesis_job_id=job.id,
    )
    
    # Get voice embedding from cache
    cache = get_redis_cache()
    voice_embedding_bytes = cache.get_embedding(effective_voice_id)

    # Queue synthesis task — route to priority queue based on mode
    from app.utils.cache_keys import CacheKeyGenerator
    cache_key = CacheKeyGenerator.generate_synthesis_key(
        text=request.text,
        voice_id=effective_voice_id,
        language=request.language,
        style=request.style,
        speed=request.speed,
        pitch=request.pitch,
        emotion=request.emotion or "",
        secondary_emotion=request.secondary_emotion or "",
        emotion_blend=request.emotion_blend,
        emotion_intensity=request.emotion_intensity,
        emotion_curve=request.emotion_curve.value if hasattr(request.emotion_curve, "value") else str(request.emotion_curve),
        prosody_template=request.prosody_template.value if request.prosody_template else "",
        prosody_template_axes=json.dumps(request.prosody_template_axes or {}, sort_keys=True),
        template_composition=json.dumps(
            [item.model_dump() for item in request.template_composition] if request.template_composition else [],
            sort_keys=True,
        ),
        auto_template=request.auto_template,
        ml_refinement=request.ml_prosody_refinement,
        phoneme_alignment=request.phoneme_alignment,
    )

    queue_name, task_priority = queue_for_mode(request.mode.value)

    try:
        task = synthesize_text_task.apply_async(
            kwargs={
                "job_id": job.id,
                "text": request.text,
                "voice_id": effective_voice_id,
                "voice_embedding_bytes": voice_embedding_bytes or b"",
                "language": request.language,
                "style": request.style,
                "speed": request.speed,
                "pitch": request.pitch,
                "emotion": request.emotion,
                "secondary_emotion": request.secondary_emotion,
                "emotion_blend": request.emotion_blend,
                "emotion_intensity": request.emotion_intensity,
                "emotion_curve": request.emotion_curve.value if hasattr(request.emotion_curve, "value") else str(request.emotion_curve),
                "prosody_template": request.prosody_template.value if request.prosody_template else None,
                "cache_key": cache_key,
                "mode": request.mode.value,
                "voice_seed": effective_seed,
                "session_id": effective_session_id,
                "prosody_template_axes": request.prosody_template_axes,
                "template_composition_data": [
                    item.model_dump() for item in request.template_composition
                ] if request.template_composition else None,
                "auto_template": request.auto_template,
                "hierarchical_prosody_config": request.hierarchical_prosody.model_dump() if request.hierarchical_prosody else None,
                "ml_refinement": request.ml_prosody_refinement,
                "phoneme_alignment": request.phoneme_alignment,
            },
            task_id=f"synthesis-{job.id}",
            queue=queue_name,
            priority=task_priority,
        )
        logger.info(
            "Synthesis job %s queued on %s (priority=%d, mode=%s)",
            job.id, queue_name, task_priority, request.mode.value,
        )

        return SynthesisResponse(
            id=job.id,
            status=job.status,
            progress=0.0,
            created_at=job.created_at,
            mode=request.mode,
            session_id=effective_session_id,
        )
    except Exception as e:
        logger.warning(f"Task queue unavailable for job {job.id}: {e}")

        if settings.SYNTHESIS_QUEUE_POLICY == "required" or not settings.TTS_ALLOW_SYNTH_FALLBACK:
            job.status = "failed"
            job.error_message = "Queue unavailable and fallback disabled"
            session.add(job)
            session.commit()
            raise HTTPException(status_code=503, detail="Synthesis queue unavailable")

        fallback_thread = threading.Thread(
            target=_run_inline_fallback_synthesis,
            kwargs={
                "job_id": job.id,
                "text": request.text,
                "voice": voice,
                "speed": request.speed,
                "pitch": request.pitch,
                "style": request.style,
                "emotion": request.emotion,
                "secondary_emotion": request.secondary_emotion,
                "emotion_blend": request.emotion_blend,
                "emotion_intensity": request.emotion_intensity,
                "emotion_curve": request.emotion_curve.value if hasattr(request.emotion_curve, "value") else str(request.emotion_curve),
                "prosody_template": request.prosody_template.value if request.prosody_template else None,
                "prosody_template_axes": request.prosody_template_axes,
                "template_composition_data": [
                    item.model_dump() for item in request.template_composition
                ] if request.template_composition else None,
                "auto_template": request.auto_template,
                "hierarchical_prosody_config": request.hierarchical_prosody.model_dump() if request.hierarchical_prosody else None,
                "session_id": effective_session_id,
                "ml_refinement": request.ml_prosody_refinement,
                "phoneme_alignment": request.phoneme_alignment,
            },
            daemon=True,
        )
        fallback_thread.start()

        return SynthesisResponse(
            id=job.id,
            status="pending",
            progress=0.0,
            created_at=job.created_at,
            mode=request.mode,
            session_id=effective_session_id,
        )


@app.post("/enhance", response_model=EnhanceResponse)
async def enhance(request: EnhanceRequest):
    """Render enhanced speech and return inline base64 audio with applied deltas."""
    if len(request.text) > 5000:
        raise HTTPException(status_code=400, detail="Text too long (max 5000 chars)")
    if len(request.text) < 1:
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    synth_engine = get_tts_engine()

    try:
        deltas = _compute_enhance_deltas(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid enhancement controls: {e}") from e

    try:
        audio, sample_rate = synth_engine.synthesize(
            text=request.text,
            speed=request.speed,
            pitch=request.pitch,
            style=request.style.value,
            mode=request.mode.value,
            voice_seed=request.voice_seed,
            emotion=request.emotion,
            emotion_secondary=request.secondary_emotion,
            emotion_blend=request.emotion_blend,
            emotion_intensity=request.emotion_intensity,
            emotion_curve=request.emotion_curve.value,
            prosody_template=request.prosody_template.value if request.prosody_template else None,
            prosody_template_axes=request.prosody_template_axes,
            auto_template=request.auto_template,
            session_id=request.session_id,
            ml_refinement=request.ml_prosody_refinement,
            phoneme_alignment=request.phoneme_alignment,
        )
    except Exception as e:
        logger.error("Enhance synthesis failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to synthesize enhanced audio") from e

    wav_buffer = io.BytesIO()
    sf.write(wav_buffer, audio, sample_rate, format="WAV")
    audio_base64 = base64.b64encode(wav_buffer.getvalue()).decode("ascii")

    return EnhanceResponse(audioBase64=audio_base64, deltas=deltas)


@app.get("/synthesis/{job_id}", response_model=SynthesisResponse)
async def get_synthesis_status(
    job_id: str,
    session: Session = Depends(get_session),
):
    """Get synthesis job status and result."""
    
    job = session.exec(select(SynthesisJob).where(SynthesisJob.id == job_id)).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return SynthesisResponse(
        id=job.id,
        status=job.status,
        audio_url=job.audio_url,
        audio_duration=job.audio_duration,
        progress=job.progress,
        created_at=job.created_at,
        completed_at=job.completed_at,
        inference_time_ms=job.inference_time_ms,
    )


@app.post("/synthesize-batch")
async def synthesize_batch(
    voice_id: str,
    texts: list[str],
    language: str = "en",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Batch synthesize multiple texts with same voice.
    
    Request:
    POST /synthesize-batch
    {
        "voice_id": "voice-123",
        "texts": [
            "Hello, how are you?",
            "This is great!",
            "See you soon."
        ],
        "language": "en"
    }
    """
    
    # Validate input
    if not texts or len(texts) == 0:
        raise HTTPException(status_code=400, detail="texts list cannot be empty")
    
    if len(texts) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 texts per batch")
    
    max_chars = sum(len(t) for t in texts)
    if max_chars > 50000:
        raise HTTPException(status_code=400, detail="Batch too large (max 50k characters)")
    
    # Verify voice exists
    voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Check quota
    from app.services.quota import get_quota_manager
    quota_mgr = get_quota_manager()
    can_proceed, remaining = quota_mgr.check_monthly_quota(user, max_chars)
    if not can_proceed:
        raise HTTPException(
            status_code=429,
            detail=f"Quota exceeded. Remaining: {remaining} characters"
        )
    
    # Create batch job
    batch_id = str(uuid.uuid4())
    jobs = []
    
    cache = get_redis_cache()
    voice_embedding_bytes = cache.get_embedding(voice_id)
    
    for text in texts:
        job = SynthesisJob(
            user_id=user.id,
            voice_id=voice_id,
            text=text,
            text_hash=str(hash(text)),
            language=language,
            style="normal",
            speed=1.0,
            pitch=1.0,
            status="pending",
        )
        session.add(job)
        jobs.append(job)
    
    session.commit()
    
    # Queue all jobs
    for job in jobs:
        synthesize_text_task.apply_async(
            kwargs={
                "job_id": job.id,
                "text": job.text,
                "voice_id": voice_id,
                "voice_embedding_bytes": voice_embedding_bytes or b"",
                "language": language,
            },
            task_id=f"synthesis-{job.id}",
        )
    
    # Deduct quota
    quota_mgr.deduct_quota(user, max_chars, session)
    
    logger.info(f"Batch synthesis created: {batch_id} with {len(jobs)} items")
    
    return {
        "batch_id": batch_id,
        "total_items": len(jobs),
        "job_ids": [job.id for job in jobs],
        "status": "queued",
        "total_characters": max_chars,
        "message": "Batch synthesis started. Poll individual jobs for status."
    }


# ============ SSML Synthesis Endpoints ============

@app.post("/ssml/validate")
async def validate_ssml(
    ssml: str,
):
    """
    Validate SSML syntax without synthesizing.
    
    Useful for testing SSML before sending synthesis request.
    """
    from app.services.ssml import validate_ssml, SSMLParser
    
    is_valid, error = validate_ssml(ssml)
    
    if not is_valid:
        return {
            "is_valid": False,
            "error": error,
        }
    
    # Extract plain text
    parser = SSMLParser()
    segments = parser.parse(ssml)
    plain_text = parser.to_plain_text()
    
    return {
        "is_valid": True,
        "plain_text": plain_text,
        "character_count": len(plain_text),
        "segment_count": len(segments),
    }


@app.post("/synthesize-ssml", response_model=SynthesisResponse)
async def synthesize_ssml(
    request: SSMLSynthesisRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Synthesize SSML with phrase-level control.
    
    SSML allows fine-grained control over:
    - <emphasis> - Emphasize text
    - <break> - Pause/silence
    - <prosody pitch="..." rate="..." volume="..."> - Adjust pitch, speed, volume
    - <phoneme> - Explicit pronunciation
    - <voice> - Switch to different voice
    
    Example:
        {
            "ssml": "<speak>Hello <emphasis level='strong'>world</emphasis>. <break time='500ms'/> How are you?</speak>",
            "voice_id": "voice-123",
            "language": "en"
        }
    """
    from app.services.ssml import SSMLParser, is_ssml
    from app.services.quota import get_quota_manager
    
    # Validate SSML
    parser = SSMLParser()
    try:
        segments = parser.parse(request.ssml)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid SSML: {str(e)}",
        )
    
    # Get plain text for quota check
    plain_text = parser.to_plain_text()
    text_length = len(plain_text)
    
    # Check quota
    quota_mgr = get_quota_manager()
    can_proceed, remaining = quota_mgr.check_monthly_quota(user, text_length)
    
    if not can_proceed:
        MetricsCollector.record_rate_limit_exceeded(user.tier, "/synthesize-ssml")
        raise HTTPException(
            status_code=429,
            detail=f"Quota exceeded. Remaining: {remaining} characters",
        )
    
    # Get voice
    voice = session.exec(select(Voice).where(Voice.id == request.voice_id)).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Create synthesis job
    job = SynthesisJob(
        text=plain_text,
        text_hash=str(hash(plain_text)),
        voice_id=request.voice_id,
        user_id=user.id,
        language=request.language,
        style="normal",
        status="pending",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    
    # Queue synthesis task
    from app.tasks.synthesis import synthesize_text_task
    
    voice_embedding_bytes = voice.speaker_embedding or b""
    
    task = synthesize_text_task.apply_async(
        kwargs={
            "job_id": job.id,
            "text": request.ssml,
            "voice_id": request.voice_id,
            "voice_embedding_bytes": voice_embedding_bytes,
            "language": request.language,
            "is_ssml": True,
        },
        task_id=f"synthesis-{job.id}",
    )
    
    # Deduct quota
    quota_mgr.deduct_quota(user, text_length, session)
    
    return {
        "id": job.id,
        "status": "pending",
        "progress": 0.0,
        "created_at": job.created_at,
    }


@app.websocket("/ws/synthesize-ssml")
async def websocket_synthesize_ssml(
    websocket: WebSocket,
    voice_id: str,
    user: User = Depends(get_current_user),
):
    """
    WebSocket endpoint for streaming SSML synthesis.
    
    Client sends SSML document, receives audio chunks in real-time.
    """
    await websocket.accept()
    
    try:
        # Receive SSML from client
        data = await websocket.receive_json()
        ssml = data.get("ssml", "")
        
        if not ssml:
            await websocket.send_json({
                "type": "error",
                "data": "Missing SSML content",
            })
            await websocket.close()
            return
        
        # Validate SSML
        from app.services.ssml import SSMLParser
        
        parser = SSMLParser()
        try:
            segments = parser.parse(ssml)
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "data": f"Invalid SSML: {str(e)}",
            })
            await websocket.close()
            return
        
        # Get voice
        from app.core.database import SessionLocal
        session = SessionLocal()
        
        voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
        if not voice:
            await websocket.send_json({
                "type": "error",
                "data": "Voice not found",
            })
            await websocket.close()
            return
        
        # Synthesize SSML
        tts_engine = get_tts_engine()
        voice_embedding = None
        
        if voice.speaker_embedding:
            import pickle
            voice_embedding = pickle.loads(voice.speaker_embedding)
        
        # Synthesize SSML (combined audio)
        audio, sr = tts_engine.synthesize_ssml(
            ssml,
            voice_embedding=voice_embedding,
            language="en",
        )
        
        # Stream audio chunks
        streaming_manager = StreamingTTSManager()
        await streaming_manager.stream_audio_chunks(
            audio,
            sr,
            websocket,
        )
        
        session.close()
    except Exception as e:
        logger.error(f"WebSocket SSML synthesis error: {e}")
        try:
            await websocket.send_json({
                "type":  "error",
                "data": str(e),
            })
        except:
            pass


# ============ Voice Endpoints ============

@app.post("/voices/create", response_model=VoiceResponse)
async def create_voice(
    request: VoiceCloningRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Create a new voice for cloning."""
    
    voice = Voice(
        owner_id=user.id,
        name=request.name,
        description=request.description,
        gender=request.gender,
        accent=request.accent,
        language=request.language,
        speaker_embedding=b"",  # Will be set when samples are uploaded
    )
    
    session.add(voice)
    session.commit()
    session.refresh(voice)
    
    logger.info(f"Voice {voice.id} created for user {user.id}")
    
    return voice


@app.get("/voices/{voice_id}", response_model=VoiceResponse)
async def get_voice(
    voice_id: str,
    session: Session = Depends(get_session),
):
    """Get voice details."""
    
    voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    if not voice.is_public:
        raise HTTPException(status_code=403, detail="Voice is private")
    
    return voice


@app.post("/voices/{voice_id}/upload-sample")
async def upload_voice_sample(
    voice_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Upload an audio sample for voice cloning with validation."""
    
    voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Verify ownership
    if voice.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    # Read audio file
    content = await file.read()
    
    if len(content) > 100 * 1024 * 1024:  # 100MB max
        raise HTTPException(status_code=413, detail="File too large (max 100MB)")
    
    # Validate audio
    try:
        from app.services.audio_validation import validate_audio, AudioValidationError
        cache = get_redis_cache()
        upload_id = str(uuid.uuid4())
        status_key = f"voice-upload:{voice_id}"
        
        audio, sr, metadata = validate_audio(content, file.filename)

        ext = os.path.splitext(file.filename or "sample.wav")[1] or ".wav"
        sample_dir = os.path.join("uploads", "voice_samples", voice_id)
        os.makedirs(sample_dir, exist_ok=True)
        sample_path = os.path.join(sample_dir, f"{upload_id}{ext}")
        with open(sample_path, "wb") as out_file:
            out_file.write(content)

        quality_score = max(0.0, min(1.0, metadata["snr"] / 40.0))
        sample = VoiceSample(
            voice_id=voice_id,
            audio_url=f"/{sample_path}",
            duration=len(audio) / sr,
            sample_rate=sr,
            quality_score=quality_score,
        )
        session.add(sample)
        session.commit()
        session.refresh(sample)

        sample_urls = json.loads(voice.sample_urls) if voice.sample_urls else []
        sample_urls.append(sample.audio_url)
        voice.sample_urls = json.dumps(sample_urls)
        voice.quality_score = max(voice.quality_score, quality_score)
        session.add(voice)
        session.commit()
        
        logger.info(
            f"Voice sample validated for {voice_id}: "
            f"SNR={metadata['snr']:.1f}dB, "
            f"Loudness={metadata['loudness']:.1f}LUFS"
        )

        cache.set_job_status(status_key, {
            "status": "processing",
            "voice_id": voice_id,
            "upload_id": upload_id,
            "sample_id": sample.id,
            "updated_at": utc_now().isoformat(),
        })

        try:
            task = encode_voice_samples_task.apply_async(
                kwargs={
                    "voice_id": voice_id,
                    "audio_bytes_list": [audio.astype(np.float32).tobytes()],
                    "sample_rates": [sr],
                },
                task_id=f"voice-encode-{voice_id}-{upload_id}",
            )
            cache.set_job_status(status_key, {
                "status": "queued",
                "voice_id": voice_id,
                "upload_id": upload_id,
                "sample_id": sample.id,
                "task_id": task.id,
                "updated_at": utc_now().isoformat(),
            })
            pipeline_status = "queued"
        except Exception as queue_error:
            logger.warning(f"Voice encoding queue unavailable for {voice_id}; using local fallback: {queue_error}")
            engine_instance = get_tts_engine()
            embedding = engine_instance.encode_voice(audio, sr)
            voice.speaker_embedding = embedding.astype(np.float32).tobytes()
            voice.updated_at = utc_now()
            session.add(voice)
            session.commit()
            cache.set_job_status(status_key, {
                "status": "completed",
                "voice_id": voice_id,
                "upload_id": upload_id,
                "sample_id": sample.id,
                "pipeline": "sync-fallback",
                "updated_at": utc_now().isoformat(),
            })
            pipeline_status = "completed"
        
        return {
            "status": pipeline_status,
            "message": "Voice sample validated and queued for processing",
            "voice_id": voice_id,
            "upload_id": upload_id,
            "sample_id": sample.id,
            "filename": file.filename,
            "metadata": {
                "duration_seconds": len(audio) / sr,
                "sample_rate": sr,
                "loudness_lufs": metadata['loudness'],
                "snr_db": metadata['snr'],
            }
        }
    
    except AudioValidationError as e:
        logger.warning(f"Audio validation failed for {voice_id}: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    
    except Exception as e:
        logger.error(f"Audio processing failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Audio processing failed")


@app.get("/voices/{voice_id}/upload-status")
async def get_voice_upload_status(
    voice_id: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Get latest upload/encoding status for a voice."""
    voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    if voice.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    cache = get_redis_cache()
    status_data = cache.get_job_status(f"voice-upload:{voice_id}")

    latest_sample = session.exec(
        select(VoiceSample)
        .where(VoiceSample.voice_id == voice_id)
        .order_by(VoiceSample.created_at.desc())
    ).first()

    return {
        "voice_id": voice_id,
        "upload_status": status_data or {"status": "unknown"},
        "latest_sample": {
            "id": latest_sample.id,
            "audio_url": latest_sample.audio_url,
            "duration": latest_sample.duration,
            "sample_rate": latest_sample.sample_rate,
            "quality_score": latest_sample.quality_score,
        } if latest_sample else None,
    }


@app.get("/voices")
async def list_voices(
    skip: int = 0,
    limit: int = 20,
    language: str = None,
    verified_only: bool = False,
    session: Session = Depends(get_session),
):
    """List all public voices with filtering options."""
    
    query = select(Voice).where(Voice.is_public == True)

    if verified_only:
        query = query.where(Voice.is_verified == True)

    if language:
        query = query.where(Voice.language == language)

    voices = session.exec(
        query.order_by(Voice.quality_score.desc(), Voice.created_at.desc()).offset(skip).limit(limit)
    ).all()
    total_count = len(session.exec(query).all())

    return {
        "total_count": total_count,
        "skip": skip,
        "limit": limit,
        "voices": voices,
    }


@app.get("/voices/{voice_id}/metadata")
async def get_voice_metadata(
    voice_id: str,
    session: Session = Depends(get_session),
):
    """Get detailed voice metadata including usage statistics."""
    
    voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    if not voice.is_public:
        raise HTTPException(status_code=403, detail="Voice is private")
    
    return {
        "id": voice.id,
        "name": voice.name,
        "description": voice.description,
        "gender": voice.gender,
        "accent": voice.accent,
        "language": voice.language,
        "is_verified": voice.is_verified,
        "quality_score": voice.quality_score,
        "total_characters_synthesized": voice.total_characters_synthesized,
        "last_used_at": voice.last_used_at,
        "created_at": voice.created_at,
        "updated_at": voice.updated_at,
    }


@app.put("/voices/{voice_id}", response_model=VoiceResponse)
async def update_voice(
    voice_id: str,
    request: VoiceUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Update voice details."""
    
    voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Verify ownership
    if voice.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    if request.name:
        voice.name = request.name
    if request.description is not None:
        voice.description = request.description
    if request.is_public is not None:
        voice.is_public = request.is_public
    
    voice.updated_at = utc_now()
    session.add(voice)
    session.commit()
    session.refresh(voice)
    
    logger.info(f"Voice {voice_id} updated")
    
    return voice


@app.delete("/voices/{voice_id}")
async def delete_voice(
    voice_id: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Delete a voice."""
    
    voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Verify ownership
    if voice.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    session.delete(voice)
    session.commit()
    
    logger.info(f"Voice {voice_id} deleted")
    
    return {"status": "deleted", "voice_id": voice_id}


@app.get("/me/voices")
async def list_my_voices(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """List voices owned by the current user."""
    
    voices = session.exec(select(Voice).where(Voice.owner_id == user.id)).all()
    
    return {
        "user_id": user.id,
        "total_voices": len(voices),
        "voices": voices,
    }


@app.post("/voices/{voice_id}/clone")
async def clone_voice(
    voice_id: str,
    new_name: str,
    new_description: str = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Clone an existing voice as a new voice."""
    
    source_voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
    if not source_voice:
        raise HTTPException(status_code=404, detail="Source voice not found")
    
    # Create new voice with cloned embedding
    cloned_voice = Voice(
        owner_id=user.id,
        name=new_name,
        description=new_description or f"Clone of {source_voice.name}",
        gender=source_voice.gender,
        accent=source_voice.accent,
        language=source_voice.language,
        speaker_embedding=source_voice.speaker_embedding,  # Same embedding
        embedding_model=source_voice.embedding_model,
        is_public=False,
    )
    
    session.add(cloned_voice)
    session.commit()
    session.refresh(cloned_voice)
    
    logger.info(f"Voice {voice_id} cloned as {cloned_voice.id}")
    
    return cloned_voice


# ============ Voice Marketplace Endpoints ============

@app.post("/voices/{voice_id}/contribute")
async def contribute_voice_to_marketplace(
    voice_id: str,
    consent: bool,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Contribute a voice to the marketplace for model training.
    
    Users who contribute voices get 2 months of free premium access.
    
    Args:
        voice_id: Voice to contribute
        consent: User grants consent to use voice for training
    """
    if not consent:
        raise HTTPException(
            status_code=400,
            detail="Consent required to contribute voice",
        )
    
    # Get voice
    voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    if voice.owner_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Can only contribute your own voices",
        )
    
    # Grant free trial
    from app.services.marketplace import get_marketplace_manager
    
    marketplace = get_marketplace_manager(session)
    grant = await marketplace.grant_voice_contribution_reward(user, voice)
    
    return {
        "status": "contributed",
        "voice_id": voice_id,
        "message": "Voice contributed to marketplace!",
        "reward": {
            "free_period_days": 60,
            "bonus_characters": marketplace.INITIAL_VOICE_DONATION_QUOTA,
            "free_period_end": grant.end_date,
        },
    }


@app.post("/voices/{contribution_id}/withdraw")
async def withdraw_voice_contribution(
    contribution_id: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """
    Withdraw a voice contribution from the marketplace.
    
    Note: Voices already used in training can't be removed from trained models,
    but won't be used in future training runs.
    """
    from app.models.db import VoiceContribution
    from app.services.marketplace import get_marketplace_manager
    
    contribution = session.exec(
        select(VoiceContribution).where(VoiceContribution.id == contribution_id)
    ).first()
    
    if not contribution:
        raise HTTPException(status_code=404, detail="Contribution not found")
    
    if contribution.user_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Can only withdraw your own contributions",
        )
    
    marketplace = get_marketplace_manager(session)
    await marketplace.withdraw_voice_contribution(contribution_id)
    
    return {
        "status": "withdrawn",
        "contribution_id": contribution_id,
        "message": "Voice contribution withdrawn from marketplace",
    }


@app.get("/me/voice-contributions")
async def get_my_voice_contributions(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Get all voices current user has contributed to marketplace."""
    from app.models.db import VoiceContribution
    from app.services.marketplace import get_marketplace_manager
    
    marketplace = get_marketplace_manager(session)
    contributions = await marketplace.get_user_voice_contributions(user)
    
    result = []
    for contrib in contributions:
        voice = session.exec(select(Voice).where(Voice.id == contrib.voice_id)).first()
        stats = await marketplace.get_voice_usage_stats(contrib.voice_id)
        
        result.append({
            "contribution_id": contrib.id,
            "voice_id": contrib.voice_id,
            "voice_name": contrib.voice_name,
            "status": contrib.status,
            "consent_granted": contrib.consent_granted,
            "times_used_in_training": contrib.times_used_in_training,
            "times_synthesized": contrib.times_synthesized,
            "usage_stats": stats,
            "created_at": contrib.created_at,
            "has_free_period": contrib.free_period_awarded,
        })
    
    return {
        "contributions": result,
        "total": len(result),
    }


@app.get("/me/free-trial")
async def get_my_free_trial_status(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Get current user's free trial status and remaining quota."""
    from app.services.marketplace import get_marketplace_manager
    
    marketplace = get_marketplace_manager(session)
    grant = await marketplace.get_active_free_period(user)
    
    if not grant:
        return {
            "has_active_trial": False,
            "message": "No active free trial",
        }
    
    now = utc_now()
    days_remaining = (grant.end_date - now).days
    
    # Get bonus quota remaining
    is_in_trial, bonus_remaining = await marketplace.check_and_apply_free_period_quota(
        user,
        0,  # Just checking
    )
    
    return {
        "has_active_trial": True,
        "start_date": grant.start_date,
        "end_date": grant.end_date,
        "days_remaining": days_remaining,
        "grant_reason": grant.grant_reason,
        "bonus_monthly_quota": grant.bonus_monthly_quota,
        "bonus_quota_remaining": bonus_remaining,
        "related_voice_id": grant.related_voice_id,
    }


@app.get("/marketplace/stats")
async def get_marketplace_stats(
    session: Session = Depends(get_session),
):
    """Get overall marketplace statistics."""
    from app.services.marketplace import get_marketplace_manager
    
    marketplace = get_marketplace_manager(session)
    stats = await marketplace.get_marketplace_stats()
    
    return {
        "marketplace": stats,
        "opportunity": {
            "message": "Help us improve! Contribute your voice and get 2 months free access.",
            "how_it_works": [
                "Create or clone a voice in Ghost Voice TTS",
                "Opt-in to contribute to our training dataset",
                "Receive 60 days free premium access",
                "Earn points as your voice helps train better models",
            ],
        },
    }


# ============ Billing Endpoints ============

@app.post("/billing/subscribe")
async def subscribe_to_tier(
    tier: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Subscribe to a pricing tier."""
    from app.services.billing import get_billing_manager, StripeError
    
    if tier not in ["starter", "pro", "enterprise"]:
        raise HTTPException(status_code=400, detail="Invalid tier")
    
    if user.tier == tier:
        raise HTTPException(status_code=400, detail="Already on this tier")
    
    billing_mgr = get_billing_manager()
    
    try:
        result = billing_mgr.create_subscription(user, tier, session)
        return result
    except StripeError as e:
        logger.error(f"Subscription creation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error during subscription: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create subscription")


@app.post("/billing/update-tier")
async def update_subscription_tier(
    tier: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Upgrade or downgrade subscription tier."""
    from app.services.billing import get_billing_manager, StripeError
    
    billing_mgr = get_billing_manager()
    
    try:
        result = billing_mgr.update_subscription_tier(user, tier, session)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StripeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/billing/cancel")
async def cancel_subscription(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Cancel subscription at end of current period."""
    from app.services.billing import get_billing_manager, StripeError
    
    billing_mgr = get_billing_manager()
    
    try:
        result = billing_mgr.cancel_subscription(user, session, at_period_end=True)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except StripeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/billing/subscription")
async def get_subscription_info(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Get current subscription info."""
    from app.schemas.tts import SubscriptionResponse
    from app.models.db import Subscription
    
    subscription = session.exec(select(Subscription).where(Subscription.user_id == user.id)).first()
    
    if not subscription:
        return SubscriptionResponse(
            tier="free",
            status="inactive",
        )
    
    return SubscriptionResponse(
        tier=subscription.tier,
        status=subscription.status,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        monthly_character_limit=subscription.monthly_character_limit,
        monthly_price=subscription.monthly_price_cents / 100,
        stripe_subscription_id=subscription.stripe_subscription_id,
    )


@app.get("/billing/upcoming-invoice")
async def get_upcoming_invoice(
    user: User = Depends(get_current_user),
):
    """Get preview of next invoice."""
    from app.services.billing import get_billing_manager
    from app.schemas.tts import UpcomingInvoiceResponse
    
    billing_mgr = get_billing_manager()
    invoice = billing_mgr.get_upcoming_invoice(user)
    
    if not invoice:
        raise HTTPException(status_code=400, detail="No upcoming invoice")
    
    return invoice


@app.get("/billing/invoices")
async def list_invoices(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    limit: int = 10,
    skip: int = 0,
):
    """List user's invoices."""
    from app.models.db import Invoice
    
    invoices = session.exec(
        select(Invoice)
        .where(Invoice.user_id == user.id)
        .order_by(Invoice.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    total_invoices = len(session.exec(select(Invoice).where(Invoice.user_id == user.id)).all())
    
    return {
        "invoices": [
            {
                "id": inv.id,
                "stripe_invoice_id": inv.stripe_invoice_id,
                "amount": inv.total_amount / 100,
                "status": inv.stripe_status,
                "period_start": inv.period_start,
                "period_end": inv.period_end,
                "paid": inv.paid,
                "pdf_url": inv.invoice_pdf_url,
            }
            for inv in invoices
        ],
        "total": total_invoices,
    }


@app.get("/billing/usage")
async def get_usage_info(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Get current usage and billing info."""
    from app.models.db import Subscription
    from app.schemas.tts import UsageResponse
    
    subscription = session.exec(select(Subscription).where(Subscription.user_id == user.id)).first()
    
    if not subscription:
        return UsageResponse(
            tier="free",
            usage_characters=user.current_month_usage_characters,
            remaining_quota=user.monthly_synthesis_quota - user.current_month_usage_characters,
            usage_cost=0.0,
            monthly_charge=0.0,
            cost_per_million_chars=0.0,
        )
    
    remaining = subscription.monthly_character_limit - user.current_month_usage_characters
    current_cost = user.current_month_cost_cents / 100
    
    pricing = settings.STRIPE_PRICING[subscription.tier]
    
    return UsageResponse(
        tier=subscription.tier,
        usage_characters=user.current_month_usage_characters,
        remaining_quota=remaining if remaining > 0 else 0,
        usage_cost=current_cost,
        monthly_charge=subscription.monthly_price_cents / 100,
        cost_per_million_chars=pricing["overage_price_per_million_cents"] / 100,
        period_end=subscription.current_period_end,
    )


@app.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    session: Session = Depends(get_session),
):
    """Handle Stripe webhooks."""
    from app.services.billing import BillingManager
    from fastapi import Request
    
    body = await request.body()
    signature = request.headers.get("stripe-signature")
    
    if not signature:
        raise HTTPException(status_code=400, detail="Missing stripe-signature")
    
    try:
        event = BillingManager.verify_webhook_signature(body.decode(), signature)
    except Exception as e:
        logger.error(f"Webhook verification failed: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    billing_mgr = get_billing_manager()
    
    # Handle different event types
    if event["type"] == "customer.subscription.updated":
        BillingManager.handle_subscription_updated(
            event["data"]["object"]["id"],
            session
        )
    
    elif event["type"] == "customer.subscription.deleted":
        BillingManager.handle_subscription_deleted(
            event["data"]["object"]["id"],
            session
        )
    
    elif event["type"] == "invoice.payment_succeeded":
        BillingManager.handle_invoice_payment_succeeded(
            event["data"]["object"]["id"],
            session
        )
    
    elif event["type"] == "invoice.payment_failed":
        BillingManager.handle_invoice_payment_failed(
            event["data"]["object"]["id"],
            session
        )
        logger.warning(f"Payment failed for invoice {event['data']['object']['id']}")
    
    return {"status": "received"}


# ============ Streaming Endpoints ============

@app.get("/synthesis/{job_id}/stream")
async def stream_synthesis(
    job_id: str,
    session: Session = Depends(get_session),
):
    """Stream audio as it's being synthesized (WebSocket alternative)."""
    
    job = session.exec(select(SynthesisJob).where(SynthesisJob.id == job_id)).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != "completed" or not job.audio_url:
        raise HTTPException(status_code=202, detail="Audio not ready")
    
    # In production, stream from S3
    return {
        "status": "ready",
        "audio_url": job.audio_url,
        "duration": job.audio_duration,
    }


@app.websocket("/ws/synthesize")
async def websocket_synthesize(
    websocket: WebSocket,
    session: Session = Depends(get_session),
):
    """
    WebSocket endpoint for real-time streaming synthesis.
    
    Client should send:
    {
        "text": "Hello, world!",
        "voice_id": "voice-123",
        "language": "en",
        "style": "normal",
        "speed": 1.0,
        "pitch": 1.0
    }
    
    Server streams back events with audio chunks.
    """
    
    await websocket.accept()
    streaming_manager = StreamingTTSManager()
    
    try:
        while True:
            # Receive synthesis request
            data = await websocket.receive_text()
            request = json.loads(data)
            
            # Validate required fields
            text = request.get("text", "")
            voice_id = request.get("voice_id", "")
            
            if not text or not voice_id:
                await websocket.send_json({
                    "type": "error",
                    "error": "Missing 'text' or 'voice_id'",
                })
                continue
            
            # Verify voice exists
            voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
            if not voice:
                await websocket.send_json({
                    "type": "error",
                    "error": "Voice not found",
                })
                continue
            
            # Get voice embedding from cache
            cache = get_redis_cache()
            voice_embedding_bytes = cache.get_embedding(voice_id)
            voice_embedding = None
            
            if voice_embedding_bytes:
                voice_embedding = np.frombuffer(voice_embedding_bytes, dtype=np.float32)
            
            # Stream synthesis
            async for event in streaming_manager.synthesize_and_stream(
                text=text,
                voice_id=voice_id,
                voice_embedding=voice_embedding,
                language=request.get("language", "en"),
                style=request.get("style", "normal"),
                speed=float(request.get("speed", 1.0)),
                pitch=float(request.get("pitch", 1.0)),
            ):
                await websocket.send_json(event)
    
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "error": str(e),
            })
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        workers=settings.WORKERS,
    )
