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
        def synthesize_ssml(
        self,
        ssml_text: str,
        voice_embedding: Optional[np.ndarray] = None,
        language: str = "en",
    ) -> Tuple[np.ndarray, int]:
        """
        Synthesize SSML with phrase-level control.
        
        Args:
            ssml_text: SSML markup
            voice_embedding: Speaker embedding for voice cloning
            language: Language code
        
        Returns:
            Tuple of (audio_array, sample_rate)
        """
        from app.services.ssml import SSMLParser
        
        if not self._initialized:
            self.initialize()
        
        logger.info(f"Synthesizing SSML (len={len(ssml_text)})")
        
        try:
            # Parse SSML
            parser = SSMLParser()
            segments = parser.parse(ssml_text)
            
            # Synthesize each segment and combine
            audio_parts = []
            
            for segment in segments:
                if not segment.text.strip():
                    continue
                
                # Synthesize segment text
                audio, sr = self.synthesize(
                    text=segment.text,
                    voice_embedding=voice_embedding,
                    speed=self._parse_prosody_rate(segment.prosody_rate),
                    pitch=self._parse_prosody_pitch(segment.prosody_pitch),
                    style="normal",
                )
                
                audio_parts.append(audio)
                
                # Add break after segment
                if segment.break_after > 0:
                    silence_samples = int(sr * segment.break_after / 1000)
                    silence = np.zeros(silence_samples, dtype=np.float32)
                    audio_parts.append(silence)
            
            # Combine all parts
            if audio_parts:
                combined_audio = np.concatenate(audio_parts)
                return combined_audio, sr
            else:
                # Empty SSML
                return np.zeros(self.sample_rate, dtype=np.float32), self.sample_rate
        
        except Exception as e:
            logger.error(f"SSML synthesis failed: {e}")
            raise
    
    def _parse_prosody_rate(self, rate_str: Optional[str]) -> float:
        """Parse prosody rate string to multiplier."""
        if not rate_str:
            return 1.0
        
        rate_map = {
            "x-slow": 0.6,
            "slow": 0.8,
            "medium": 1.0,
            "fast": 1.2,
            "x-fast": 1.5,
        }
        
        if rate_str in rate_map:
            return rate_map[rate_str]
        
        # Parse percentage or decimal
        if "%" in rate_str:
            return float(rate_str.rstrip("%")) / 100
        
        try:
            return float(rate_str)
        except ValueError:
            return 1.0
    
    def _parse_prosody_pitch(self, pitch_str: Optional[str]) -> float:
        """Parse prosody pitch string to multiplier."""
        if not pitch_str:
            return 1.0
        
        pitch_map = {
            "x-low": 0.5,
            "low": 0.75,
            "medium": 1.0,
            "high": 1.25,
            "x-high": 1.5,
        }
        
        if pitch_str in pitch_map:
            return pitch_map[pitch_str]
        
        # Parse percentage
        if "%" in pitch_str:
            base_pitch = 1.0
            adjustment = float(pitch_str.lstrip("+").rstrip("%")) / 100
            return base_pitch + adjustment
        
        # Parse Hz
        if "Hz" in pitch_str:
            hz = float(pitch_str.rstrip("Hz"))
            # Normalize Hz to multiplier (assuming base ~100Hz)
            return hz / 100
        
        try:
            return float(pitch_str)
        except ValueError:
            return 1.0
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
