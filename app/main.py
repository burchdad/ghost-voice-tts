from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header, WebSocket, WebSocketDisconnect
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
    HealthResponse, UserResponse,
)
from app.models.db import User, Voice, SynthesisJob
from app.services.tts_engine import get_tts_engine
from app.services.cache import get_redis_cache
from app.services.streaming import StreamingTTSManager
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
