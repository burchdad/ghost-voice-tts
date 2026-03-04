from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session
import logging
from datetime import datetime
import uuid
import json
import numpy as np

from app.core.config import get_settings
from app.core.database import create_db_and_tables, get_session
from app.core.logging import setup_logging
from app.core.metrics import metrics_registry, MetricsCollector
from app.schemas.tts import (
    SynthesisRequest, SynthesisResponse,
    VoiceResponse, VoiceCloningRequest, VoiceUpdate,
    HealthResponse, UserResponse, SSMLSynthesisRequest,
)
from app.models.db import User, Voice, SynthesisJob
from app.services.tts_engine import get_tts_engine
from app.services.cache import get_redis_cache
from app.services.streaming import StreamingTTSManager
from app.services.security import get_security_manager, TokenResponse
from app.tasks.synthesis import synthesize_text_task, encode_voice_samples_task
from app.middleware import setup_middlewares
from app.dependencies import get_current_user, get_current_user_optional

# Setup
setup_logging()
logger = logging.getLogger(__name__)
settings = get_settings()

# Create FastAPI app
app = FastAPI(
    title=settings.API_TITLE,
    description=settings.API_DESCRIPTION,
    version=settings.API_VERSION,
    debug=settings.DEBUG,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup security, rate limiting, and observability middleware
setup_middlewares(app)


# ============ Startup & Shutdown ============

@app.on_event("startup")
async def startup_event():
    """Initialize on startup."""
    logger.info("Starting Ghost Voice TTS service...")
    create_db_and_tables()
    
    # Warm up TTS engine - keeps models loaded in memory
    if not settings.DEBUG:
        try:
            engine = get_tts_engine()
            engine.warm_load()
            logger.info("TTS engine warmed up and ready for low-latency inference")
        except Exception as e:
            logger.warning(f"TTS engine warmup failed: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Shutting down Ghost Voice TTS service...")
    cache = get_redis_cache()
    cache.close()


# ============ Health & Status Endpoints ============

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    cache = get_redis_cache()
    db_status = "connected"
    redis_status = "connected" if cache.health_check() else "disconnected"
    
    return HealthResponse(
        status="healthy",
        version=settings.API_VERSION,
        database=db_status,
        redis=redis_status,
        tts_model=settings.TTS_MODEL,
        timestamp=datetime.utcnow(),
    )


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint (simplified)."""
    cache = get_redis_cache()
    
    return {
        "total_syntheses": cache.get_counter("syntheses:total"),
        "total_characters": cache.get_counter("characters:total"),
        "active_jobs": cache.get_counter("jobs:active"),
        "timestamp": datetime.utcnow(),
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
    existing = session.query(User).filter(
        (User.email == email) | (User.username == username)
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
    user = session.query(User).filter(User.email == email).first()
    
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
    authorization: str = Header(None),
    session: Session = Depends(get_session),
):
    """
    Check if user can synthesize text of given length.
    
    Useful for client-side pre-checks before synthesis.
    """
    
    if text_length <= 0 or text_length > 5000:
        raise HTTPException(status_code=400, detail="Invalid text length")
    
    user = session.query(User).filter(User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
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
    authorization: str = Header(None),
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
    voice = session.query(Voice).filter(Voice.id == request.voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Get user from auth
    user = session.query(User).filter(User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
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
    
    # Deduct quota
    quota_mgr.deduct_quota(user, len(request.text), session)
    
    # Get voice embedding from cache
    cache = get_redis_cache()
    voice_embedding_bytes = cache.get_embedding(request.voice_id)
    
    # Queue synthesis task
    from app.utils.cache_keys import CacheKeyGenerator
    cache_key = CacheKeyGenerator.generate_synthesis_key(
        text=request.text,
        voice_id=request.voice_id,
        language=request.language,
        style=request.style,
        speed=request.speed,
        pitch=request.pitch,
    )
    
    task = synthesize_text_task.apply_async(
        kwargs={
            "job_id": job.id,
            "text": request.text,
            "voice_id": request.voice_id,
            "voice_embedding_bytes": voice_embedding_bytes or b"",
            "language": request.language,
            "style": request.style,
            "speed": request.speed,
            "pitch": request.pitch,
            "cache_key": cache_key,
        },
        task_id=f"synthesis-{job.id}",
    )
    
    logger.info(f"Synthesis job {job.id} queued with task {task.id}")
    
    return SynthesisResponse(
        id=job.id,
        status=job.status,
        progress=0.0,
        created_at=job.created_at,
    )


@app.get("/synthesis/{job_id}", response_model=SynthesisResponse)
async def get_synthesis_status(
    job_id: str,
    session: Session = Depends(get_session),
):
    """Get synthesis job status and result."""
    
    job = session.query(SynthesisJob).filter(SynthesisJob.id == job_id).first()
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
    authorization: str = Header(None),
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
    
    # Get user and verify
    user = session.query(User).filter(User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Verify voice exists
    voice = session.query(Voice).filter(Voice.id == voice_id).first()
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
    voice = session.query(Voice).filter(Voice.id == request.voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Create synthesis job
    job = SynthesisJob(
        text=plain_text,
        ssml=request.ssml,
        voice_id=request.voice_id,
        user_id=user.id,
        language=request.language,
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
        
        voice = session.query(Voice).filter(Voice.id == voice_id).first()
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
    authorization: str = Header(None),
    session: Session = Depends(get_session),
):
    """Create a new voice for cloning."""
    
    # Get user from auth
    user = session.query(User).filter(User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
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
    
    voice = session.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    if not voice.is_public:
        raise HTTPException(status_code=403, detail="Voice is private")
    
    return voice


@app.post("/voices/{voice_id}/upload-sample")
async def upload_voice_sample(
    voice_id: str,
    file: UploadFile = File(...),
    authorization: str = Header(None),
    session: Session = Depends(get_session),
):
    """Upload an audio sample for voice cloning with validation."""
    
    voice = session.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Verify ownership
    user = session.query(User).filter(User.is_active == True).first()
    if not user or voice.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    # Read audio file
    content = await file.read()
    
    if len(content) > 100 * 1024 * 1024:  # 100MB max
        raise HTTPException(status_code=413, detail="File too large (max 100MB)")
    
    # Validate audio
    try:
        from app.services.audio_validation import validate_audio, AudioValidationError
        
        audio, sr, metadata = validate_audio(content, file.filename)
        
        logger.info(
            f"Voice sample validated for {voice_id}: "
            f"SNR={metadata['snr']:.1f}dB, "
            f"Loudness={metadata['loudness']:.1f}LUFS"
        )
        
        # TODO: In production, upload to S3 and queue voice encoding task
        # from app.tasks.synthesis import encode_voice_samples_task
        # encode_voice_samples_task.apply_async(kwargs={...})
        
        return {
            "status": "received",
            "message": "Voice sample validated and queued for processing",
            "voice_id": voice_id,
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


@app.get("/voices")
async def list_voices(
    skip: int = 0,
    limit: int = 20,
    language: str = None,
    verified_only: bool = False,
    session: Session = Depends(get_session),
):
    """List all public voices with filtering options."""
    
    query = session.query(Voice).filter(Voice.is_public == True)
    
    if verified_only:
        query = query.filter(Voice.is_verified == True)
    
    if language:
        query = query.filter(Voice.language == language)
    
    # Order by quality score and creation date
    voices = query.order_by(
        Voice.quality_score.desc(),
        Voice.created_at.desc()
    ).offset(skip).limit(limit).all()
    
    return {
        "total_count": query.count(),
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
    
    voice = session.query(Voice).filter(Voice.id == voice_id).first()
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
    authorization: str = Header(None),
    session: Session = Depends(get_session),
):
    """Update voice details."""
    
    voice = session.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Verify ownership
    user = session.query(User).filter(User.is_active == True).first()
    if not user or voice.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    if request.name:
        voice.name = request.name
    if request.description is not None:
        voice.description = request.description
    if request.is_public is not None:
        voice.is_public = request.is_public
    
    voice.updated_at = datetime.utcnow()
    session.add(voice)
    session.commit()
    session.refresh(voice)
    
    logger.info(f"Voice {voice_id} updated")
    
    return voice


@app.delete("/voices/{voice_id}")
async def delete_voice(
    voice_id: str,
    authorization: str = Header(None),
    session: Session = Depends(get_session),
):
    """Delete a voice."""
    
    voice = session.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Verify ownership
    user = session.query(User).filter(User.is_active == True).first()
    if not user or voice.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    session.delete(voice)
    session.commit()
    
    logger.info(f"Voice {voice_id} deleted")
    
    return {"status": "deleted", "voice_id": voice_id}


@app.get("/me/voices")
async def list_my_voices(
    authorization: str = Header(None),
    session: Session = Depends(get_session),
):
    """List voices owned by the current user."""
    
    user = session.query(User).filter(User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    voices = session.query(Voice).filter(Voice.owner_id == user.id).all()
    
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
    authorization: str = Header(None),
    session: Session = Depends(get_session),
):
    """Clone an existing voice as a new voice."""
    
    source_voice = session.query(Voice).filter(Voice.id == voice_id).first()
    if not source_voice:
        raise HTTPException(status_code=404, detail="Source voice not found")
    
    user = session.query(User).filter(User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
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
    voice = session.query(Voice).filter(Voice.id == voice_id).first()
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
    
    contribution = session.query(VoiceContribution).filter(
        VoiceContribution.id == contribution_id,
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
        voice = session.query(Voice).filter(Voice.id == contrib.voice_id).first()
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
    
    now = datetime.datetime.utcnow()
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


# ============ Streaming Endpoints ============

@app.get("/synthesis/{job_id}/stream")
async def stream_synthesis(
    job_id: str,
    session: Session = Depends(get_session),
):
    """Stream audio as it's being synthesized (WebSocket alternative)."""
    
    job = session.query(SynthesisJob).filter(SynthesisJob.id == job_id).first()
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
            voice = session.query(Voice).filter(Voice.id == voice_id).first()
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
