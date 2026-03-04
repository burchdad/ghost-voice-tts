import logging
import numpy as np
from datetime import datetime
import time

from app.core.celery import celery_app
from app.services.tts_engine import get_tts_engine
from app.services.cache import get_redis_cache
from app.services.streaming import StreamingTTSManager
from app.utils.cache_keys import CacheKeyGenerator
from app.core.database import engine
from app.models.db import SynthesisJob
from app.core.metrics import MetricsCollector
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.tasks.synthesis.synthesize_text")
def synthesize_text_task(
    self,
    job_id: str,
    text: str,
    voice_id: str,
    voice_embedding_bytes: bytes,
    language: str = "en",
    style: str = "normal",
    speed: float = 1.0,
    pitch: float = 1.0,
    cache_key: str = None,
) -> dict:
    """
    Async task to synthesize text to speech.
    
    Args:
        job_id: Synthesis job ID
        text: Text to synthesize
        voice_id: Voice ID for speaker embedding
        voice_embedding_bytes: Serialized speaker embedding
        language: Target language
        style: Speech style
        speed: Speech rate
        pitch: Pitch adjustment
        cache_key: Cache key for result
    
    Returns:
        Task result with audio URL and metadata
    """
    
    cache = get_redis_cache()
    start_time = time.time()
    
    try:
        # Update job status to processing
        with Session(engine) as session:
            job = session.exec(select(SynthesisJob).where(SynthesisJob.id == job_id)).first()
            if job:
                job.status = "processing"
                job.progress = 0.1
                session.add(job)
                session.commit()
        
        self.update_state(state="PROGRESS", meta={"progress": 10})
        
        # Check cache first
        if cache_key:
            cached_audio = cache.get_audio(cache_key)
            if cached_audio:
                logger.info(f"Using cached audio for job {job_id}")
                MetricsCollector.record_cache_hit("audio")
                with Session(engine) as session:
                    job = session.exec(select(SynthesisJob).where(SynthesisJob.id == job_id)).first()
                    if job:
                        job.status = "completed"
                        job.is_cached = True
                        job.progress = 1.0
                        job.completed_at = datetime.utcnow()
                        inference_time = (time.time() - start_time) * 1000
                        job.total_time_ms = inference_time
                        session.add(job)
                        session.commit()
                
                return {
                    "job_id": job_id,
                    "status": "completed",
                    "cached": True,
                    "total_time_ms": (time.time() - start_time) * 1000,
                }
        
        self.update_state(state="PROGRESS", meta={"progress": 20})
        
        # Deserialize voice embedding
        voice_embedding = None
        if voice_embedding_bytes:
            voice_embedding = np.frombuffer(voice_embedding_bytes, dtype=np.float32)
        
        # Check cache miss
        if cache_key:
            MetricsCollector.record_cache_miss("audio")
        
        # Get TTS engine and synthesize
        engine_instance = get_tts_engine()
        audio_array, sample_rate = engine_instance.synthesize(
            text=text,
            voice_embedding=voice_embedding,
            speed=speed,
            pitch=pitch,
            style=style,
        )
        
        self.update_state(state="PROGRESS", meta={"progress": 70})
        
        # Convert audio to bytes
        audio_bytes = audio_array.astype(np.float32).tobytes()
        audio_duration = len(audio_array) / sample_rate
        
        # Cache the result
        if cache_key:
            cache.set_audio(cache_key, audio_bytes)
        
        # In production, upload to S3 here
        # For now, we'll store metadata
        audio_url = f"s3://ghost-voice-tts/audio/{job_id}.wav"
        
        self.update_state(state="PROGRESS", meta={"progress": 90})
        
        # Update job in database
        with Session(engine) as session:
            job = session.exec(select(SynthesisJob).where(SynthesisJob.id == job_id)).first()
            if job:
                inference_time = (time.time() - start_time) * 1000
                job.status = "completed"
                job.audio_url = audio_url
                job.audio_duration = audio_duration
                job.progress = 1.0
                job.completed_at = datetime.utcnow()
                job.inference_time_ms = inference_time
                job.total_time_ms = inference_time
                session.add(job)
                session.commit()
        
        total_time = (time.time() - start_time) * 1000
        logger.info(f"Synthesis completed for job {job_id} in {total_time:.2f}ms")
        
        # Record metrics
        MetricsCollector.record_synthesis_complete(
            duration=total_time / 1000.0,
            success=True,
            num_characters=len(text),
        )
        MetricsCollector.record_inference_time(inference_time)
        
        return {
            "job_id": job_id,
            "status": "completed",
            "audio_url": audio_url,
            "audio_duration": audio_duration,
            "inference_time_ms": inference_time,
            "total_time_ms": total_time,
        }
    
    except Exception as e:
        logger.error(f"Synthesis failed for job {job_id}: {e}", exc_info=True)
        
        # Update job status to failed
        try:
            with Session(engine) as session:
                job = session.exec(select(SynthesisJob).where(SynthesisJob.id == job_id)).first()
                if job:
                    job.status = "failed"
                    job.error_message = str(e)
                    job.retry_count += 1
                    
                    # Retry if not exceeded limit
                    if job.retry_count < job.max_retries:
                        logger.info(f"Retrying job {job_id} (attempt {job.retry_count})")
                        job.status = "pending"
                    
                    session.add(job)
                    session.commit()
        except Exception as db_error:
            logger.error(f"Failed to update job status: {db_error}")
        
        raise


@celery_app.task(bind=True, name="app.tasks.voice_cloning.encode_voice_samples")
def encode_voice_samples_task(
    self,
    voice_id: str,
    audio_bytes_list: list[bytes],
    sample_rates: list[int],
) -> dict:
    """
    Encode voice samples to speaker embedding.
    
    Args:
        voice_id: Voice ID to associate embedding with
        audio_bytes_list: List of audio samples as bytes
        sample_rates: Sample rates for each audio
    
    Returns:
        Task result with embedding metadata
    """
    
    try:
        engine_instance = get_tts_engine()
        
        embeddings = []
        for i, (audio_bytes, sr) in enumerate(zip(audio_bytes_list, sample_rates)):
            # Convert bytes back to numpy array
            audio = np.frombuffer(audio_bytes, dtype=np.float32)
            
            # Encode to embedding
            embedding = engine_instance.encode_voice(audio, sr)
            embeddings.append(embedding)
            
            self.update_state(state="PROGRESS", meta={"progress": (i + 1) / len(audio_bytes_list) * 100})
        
        # Average embeddings
        avg_embedding = np.mean(embeddings, axis=0)
        
        # Cache the embedding
        cache = get_redis_cache()
        cache.set_embedding(voice_id, avg_embedding.tobytes())
        
        logger.info(f"Voice encoding completed for voice_id {voice_id}")
        
        return {
            "voice_id": voice_id,
            "samples_processed": len(audio_bytes_list),
            "embedding_cached": True,
        }
    
    except Exception as e:
        logger.error(f"Voice encoding failed: {e}", exc_info=True)
        raise
