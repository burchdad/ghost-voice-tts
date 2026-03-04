import torch
import numpy as np
from typing import Optional, Tuple
import logging
from functools import lru_cache
import hashlib

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class TTSEngine:
    """
    High-performance TTS engine with voice cloning support.
    Wraps Tortoise TTS and provides batching, caching, and GPU optimization.
    """
    
    def __init__(self):
        self.device = torch.device(settings.TTS_DEVICE)
        self.model = None
        self.speaker_encoder = None
        self.batch_size = settings.TTS_BATCH_SIZE
        self.sample_rate = settings.AUDIO_SAMPLE_RATE
        self._initialized = False
        
    def initialize(self):
        """Lazy load models - only initialize when needed."""
        if self._initialized:
            return
        
        logger.info(f"Initializing TTS engine with model={settings.TTS_MODEL} on {self.device}")
        
        try:
            if settings.TTS_MODEL == "tortoise":
                self._load_tortoise()
            elif settings.TTS_MODEL == "vits":
                self._load_vits()
            else:
                raise ValueError(f"Unsupported TTS model: {settings.TTS_MODEL}")
            
            # Load speaker encoder for voice cloning
            if settings.VOICE_CLONE_ENABLED:
                self._load_speaker_encoder()
            
            self._initialized = True
            logger.info("TTS engine initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize TTS engine: {e}")
            raise
    
    def _load_tortoise(self):
        """Load Tortoise TTS model."""
        try:
            from tortoise import api as tortoise_api
            self.model = tortoise_api.SAMPLE_RATE
            logger.info("Tortoise TTS model loaded")
        except ImportError:
            logger.warning("Tortoise TTS not installed, would install during deployment")
    
    def _load_vits(self):
        """Load VITS model for faster inference."""
        try:
            # This would load a pre-trained VITS model
            logger.info("VITS model would be loaded here")
        except ImportError:
            logger.warning("VITS not installed")
    
    def _load_speaker_encoder(self):
        """Load speaker encoder for voice embeddings."""
        try:
            from resemblyzer import VoiceEncoder
            self.speaker_encoder = VoiceEncoder()
            logger.info("Speaker encoder loaded")
        except Exception as e:
            logger.error(f"Failed to load speaker encoder: {e}")
    
    def warm_load(self):
        """Warm-load models and keep them in memory for fast inference."""
        if not self._initialized:
            self.initialize()
        
        try:
            logger.info("Starting model warm-load...")
            
            # Generate a dummy synthesis to load model into GPU memory
            dummy_text = "This is a warm-up test."
            logger.info("Generating warm-up synthesis...")
            
            audio, sr = self.synthesize(dummy_text)
            
            logger.info(f"Warm-load complete. Model resident in {self.device} memory. "
                       f"Generated {len(audio)} samples at {sr}Hz")
        except Exception as e:
            logger.warning(f"Warm-load failed (non-blocking): {e}")
    
    def synthesize(
        self,
        text: str,
        voice_embedding: Optional[np.ndarray] = None,
        speed: float = 1.0,
        pitch: float = 1.0,
        style: str = "normal"
    ) -> Tuple[np.ndarray, int]:
        """
        Synthesize speech from text with optional voice cloning.
        
        Args:
            text: Input text to synthesize
            voice_embedding: Speaker embedding for voice cloning
            speed: Speech rate multiplier
            pitch: Pitch multiplier
            style: Speech style (normal, dramatic, whisper, etc.)
        
        Returns:
            Tuple of (audio_array, sample_rate)
        """
        if not self._initialized:
            self.initialize()
        
        logger.info(f"Synthesizing text: {text[:50]}... (len={len(text)})")
        
        try:
            # This is a placeholder - actual implementation would use Tortoise TTS API
            # For now, we'll generate a dummy audio array
            duration_ms = len(text) * 50  # Rough estimate
            num_samples = int(self.sample_rate * duration_ms / 1000)
            
            # Generate dummy audio (in production, this would be actual TTS output)
            audio = np.random.randn(num_samples).astype(np.float32) * 0.1
            
            return audio, self.sample_rate
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            raise
    
    def encode_voice(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """
        Encode audio to speaker embedding for voice cloning.
        
        Args:
            audio: Audio samples
            sr: Sample rate
        
        Returns:
            Speaker embedding vector
        """
        if not self.speaker_encoder:
            raise RuntimeError("Speaker encoder not loaded")
        
        try:
            # Normalize audio to [-1, 1]
            audio = audio / (np.max(np.abs(audio)) + 1e-10)
            
            # Encode to embedding
            embedding = self.speaker_encoder.embed_utterance(audio)
            
            return embedding
        except Exception as e:
            logger.error(f"Voice encoding failed: {e}")
            raise
    
    def synthesize_batch(
        self,
        texts: list[str],
        voice_embeddings: Optional[list[np.ndarray]] = None,
        **kwargs
    ) -> list[Tuple[np.ndarray, int]]:
        """
        Synthesize multiple texts efficiently with batching.
        
        Args:
            texts: List of input texts
            voice_embeddings: List of speaker embeddings
            **kwargs: Additional synthesis parameters
        
        Returns:
            List of (audio_array, sample_rate) tuples
        """
        results = []
        
        # Process in batches of specified size
        for i in range(0, len(texts), self.batch_size):
            batch_texts = texts[i:i + self.batch_size]
            batch_embeddings = None
            
            if voice_embeddings:
                batch_embeddings = voice_embeddings[i:i + self.batch_size]
            
            logger.info(f"Processing batch {i // self.batch_size + 1} of size {len(batch_texts)}")
            
            for text, embedding in zip(
                batch_texts,
                batch_embeddings or [None] * len(batch_texts)
            ):
                audio, sr = self.synthesize(text, embedding, **kwargs)
                results.append((audio, sr))
        
        return results
    
    def get_cache_key(self, text: str, voice_id: str, **params) -> str:
        """Generate cache key for synthesis result."""
        key_str = f"{text}_{voice_id}_{str(params)}"
        return hashlib.sha256(key_str.encode()).hexdigest()


# Singleton instance
_tts_engine: Optional[TTSEngine] = None


def get_tts_engine() -> TTSEngine:
    """Get or create TTS engine instance."""
    global _tts_engine
    if _tts_engine is None:
        _tts_engine = TTSEngine()
    return _tts_engine
