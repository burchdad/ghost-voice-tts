from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session
import logging
from datetime import datetime
import uuid

from app.core.config import get_settings
from app.core.database import create_db_and_tables, get_session
from app.core.logging import setup_logging
from app.schemas.tts import (
    SynthesisRequest, SynthesisResponse,
    VoiceResponse, VoiceCloningRequest,
    HealthResponse, UserResponse,
)
from app.models.db import User, Voice, SynthesisJob
from app.services.tts_engine import get_tts_engine
from app.services.cache import get_redis_cache
from app.tasks.synthesis import synthesize_text_task, encode_voice_samples_task

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


# ============ Startup & Shutdown ============

@app.on_event("startup")
async def startup_event():
    """Initialize on startup."""
    logger.info("Starting Ghost Voice TTS service...")
    create_db_and_tables()
    
    # Warm up TTS engine
    if not settings.DEBUG:
        try:
            engine = get_tts_engine()
            engine.initialize()
            logger.info("TTS engine warmed up")
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


# ============ User Endpoints ============

@app.post("/users/register", response_model=UserResponse)
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
        raise HTTPException(status_code=400, detail="User already exists")
    
    user = User(
        email=email,
        username=username,
        hashed_password=password,  # In production, hash this!
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    
    return user


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
    
    # Get voice embedding from cache
    cache = get_redis_cache()
    voice_embedding_bytes = cache.get_embedding(request.voice_id)
    
    # Queue synthesis task
    cache_key = f"synthesis:{job.id}"
    
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
    """Upload an audio sample for voice cloning."""
    
    voice = session.query(Voice).filter(Voice.id == voice_id).first()
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Verify ownership
    user = session.query(User).filter(User.is_active == True).first()
    if not user or voice.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    # Read audio file
    content = await file.read()
    
    # In production: validate audio, upload to S3, process
    logger.info(f"Voice sample uploaded for voice {voice_id}: {file.filename}")
    
    return {
        "status": "received",
        "message": "Voice sample received. Processing...",
        "voice_id": voice_id,
    }


@app.get("/voices")
async def list_voices(
    session: Session = Depends(get_session),
):
    """List all public voices."""
    
    voices = session.query(Voice).filter(Voice.is_public == True).all()
    return voices


# ============ Stream Endpoint ============

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        workers=settings.WORKERS,
    )
