import logging
import numpy as np
from datetime import datetime, timezone
import time
import os
import io
import soundfile as sf
try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover
    boto3 = None
    BotoCoreError = Exception
    ClientError = Exception

from app.core.celery import celery_app
from app.core.config import get_settings
from app.services.tts_engine import get_tts_engine
from app.services.cache import get_redis_cache
from app.services.streaming import StreamingTTSManager
from app.utils.cache_keys import CacheKeyGenerator
from app.core.database import engine
from app.models.db import SynthesisJob, Voice
from app.core.metrics import MetricsCollector
from app.services.prosody_intelligence import ProsodyEvaluator, ProsodyLearningStore
from sqlmodel import Session, select

logger = logging.getLogger(__name__)
settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _persist_audio_artifact(job_id: str, audio_array: np.ndarray, sample_rate: int) -> str:
    """Persist synthesized audio and return a resolvable URL."""
    wav_buffer = io.BytesIO()
    sf.write(wav_buffer, audio_array, sample_rate, format="WAV")
    wav_buffer.seek(0)

    if boto3 and settings.S3_ACCESS_KEY and settings.S3_SECRET_KEY and settings.S3_BUCKET_NAME:
        key = f"audio/{job_id}.wav"
        client_kwargs = {
            "aws_access_key_id": settings.S3_ACCESS_KEY,
            "aws_secret_access_key": settings.S3_SECRET_KEY,
            "region_name": settings.S3_REGION,
        }
        if settings.S3_ENDPOINT_URL:
            client_kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL

        try:
            s3_client = boto3.client("s3", **client_kwargs)
            s3_client.upload_fileobj(
                wav_buffer,
                settings.S3_BUCKET_NAME,
                key,
                ExtraArgs={"ContentType": "audio/wav"},
            )
            if settings.PUBLIC_BASE_URL:
                return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/{key}"
            if settings.S3_ENDPOINT_URL:
                return f"{settings.S3_ENDPOINT_URL.rstrip('/')}/{settings.S3_BUCKET_NAME}/{key}"
            return f"https://{settings.S3_BUCKET_NAME}.s3.{settings.S3_REGION}.amazonaws.com/{key}"
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to upload audio artifact to S3 for job {job_id}: {e}")

    output_dir = os.path.join("uploads", "audio")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{job_id}.wav")
    with open(output_path, "wb") as out_file:
        out_file.write(wav_buffer.getvalue())

    if settings.PUBLIC_BASE_URL:
        return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{settings.STATIC_AUDIO_PREFIX}/{job_id}.wav"
    return f"{settings.STATIC_AUDIO_PREFIX}/{job_id}.wav"


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
    emotion: str = None,
    secondary_emotion: str = None,
    emotion_blend: float = 0.0,
    emotion_intensity: float = 1.0,
    emotion_curve: str = "arc",
    prosody_template: str = None,
    cache_key: str = None,
    mode: str = "balanced",
    voice_seed: int = None,
    session_id: str = None,
    prosody_template_axes: dict = None,
    template_composition_data: list = None,
    auto_template: bool = False,
    hierarchical_prosody_config: dict = None,
    ml_refinement: bool = True,
    phoneme_alignment: bool = True,
) -> dict:
    """
    Async task to synthesize text to speech with optional emotion modulation.

    Args:
        job_id:               Synthesis job ID
        text:                 Text to synthesize
        voice_id:             Voice ID for speaker embedding
        voice_embedding_bytes: Serialized speaker embedding
        language:             Target language
        style:                Speech style
        speed:                Speech rate
        pitch:                Pitch adjustment
        emotion:              Emotion to apply (excited, calm, etc.)
        secondary_emotion:    Optional second emotion for blending
        emotion_blend:        Blend ratio for secondary emotion
        emotion_intensity:    Global emotion intensity multiplier
        emotion_curve:        Temporal contour across the utterance
        prosody_template:     Named preset that overrides emotion fields when set
        cache_key:            Cache key for result
        mode:                 Latency tier (realtime | balanced | high_quality)
        voice_seed:           Deterministic voice seed for consistency
        session_id:           Session identifier for continuity tracking

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
                        job.completed_at = utc_now()
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
            mode=mode,
            voice_seed=voice_seed,
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
        
        self.update_state(state="PROGRESS", meta={"progress": 70})
        
        # Convert audio to bytes
        audio_bytes = audio_array.astype(np.float32).tobytes()
        audio_duration = len(audio_array) / sample_rate
        
        # Cache the result
        if cache_key:
            cache.set_audio(cache_key, audio_bytes)

        # Prosody evaluation loop (lightweight heuristic scoring for telemetry).
        try:
            detected_pause_count = sum(text.count(p) for p in (",", ";", ":", ".", "!", "?"))
            score = ProsodyEvaluator.score(
                text=text,
                audio_duration_sec=audio_duration,
                expected_curve=emotion_curve,
                target_emotion_intensity=emotion_intensity,
                detected_pause_count=detected_pause_count,
            )
            MetricsCollector.record_prosody_scores(
                mode=mode,
                naturalness=score.naturalness,
                emotional_accuracy=score.emotional_accuracy,
                timing_correctness=score.timing_correctness,
                composite=score.composite,
            )

            provider_name = settings.TTS_MODEL if getattr(engine_instance, "_supports_real_synthesis", False) else "fallback"
            ProsodyLearningStore.record_feedback(
                session_id=session_id,
                provider_name=provider_name,
                quality_composite=score.composite,
                naturalness=score.naturalness,
                emotional_accuracy=score.emotional_accuracy,
                timing_correctness=score.timing_correctness,
            )
        except Exception as exc:
            logger.debug("Prosody scoring skipped: %s", exc)
        
        audio_url = _persist_audio_artifact(job_id, audio_array, sample_rate)
        
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
                job.completed_at = utc_now()
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
        cache = get_redis_cache()

        cache.set_job_status(
            f"voice-upload:{voice_id}",
            {
                "status": "processing",
                "voice_id": voice_id,
                "updated_at": utc_now().isoformat(),
            },
        )
        
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
        cache.set_embedding(voice_id, avg_embedding.tobytes())

        # Persist embedding on voice record for durable retrieval.
        with Session(engine) as session:
            voice = session.exec(select(Voice).where(Voice.id == voice_id)).first()
            if voice:
                voice.speaker_embedding = avg_embedding.astype(np.float32).tobytes()
                voice.updated_at = utc_now()
                session.add(voice)
                session.commit()

        cache.set_job_status(
            f"voice-upload:{voice_id}",
            {
                "status": "completed",
                "voice_id": voice_id,
                "samples_processed": len(audio_bytes_list),
                "updated_at": utc_now().isoformat(),
            },
        )
        
        logger.info(f"Voice encoding completed for voice_id {voice_id}")
        
        return {
            "voice_id": voice_id,
            "samples_processed": len(audio_bytes_list),
            "embedding_cached": True,
        }
    
    except Exception as e:
        cache = get_redis_cache()
        cache.set_job_status(
            f"voice-upload:{voice_id}",
            {
                "status": "failed",
                "voice_id": voice_id,
                "error": str(e),
                "updated_at": utc_now().isoformat(),
            },
        )
        logger.error(f"Voice encoding failed: {e}", exc_info=True)
        raise
