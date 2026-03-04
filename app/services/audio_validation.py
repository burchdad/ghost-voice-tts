import logging
import numpy as np
import soundfile as sf
import io
from typing import Tuple, Optional
import librosa

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class AudioValidationError(Exception):
    """Raised when audio validation fails."""
    pass


class AudioValidator:
    """Validate and preprocess audio for voice cloning."""
    
    # Supported formats
    SUPPORTED_FORMATS = ['wav', 'mp3', 'ogg', 'flac', 'm4a']
    
    # Constraints
    MIN_DURATION = settings.MIN_VOICE_SAMPLE_DURATION  # seconds
    MAX_DURATION = settings.MAX_VOICE_SAMPLE_DURATION  # seconds
    TARGET_SAMPLE_RATE = settings.AUDIO_SAMPLE_RATE
    
    # Quality thresholds
    MIN_LOUDNESS = -40.0  # LUFS (loudness units relative to full scale)
    MAX_LOUDNESS = -4.0
    MIN_SNR = 20.0  # dB (signal-to-noise ratio)
    
    @classmethod
    def validate_audio_file(
        cls,
        audio_bytes: bytes,
        filename: str,
    ) -> Tuple[np.ndarray, int, dict]:
        """
        Validate and parse audio file.
        
        Returns:
            (audio_array, sample_rate, metadata)
        
        Raises:
            AudioValidationError
        """
        
        try:
            # Get file extension
            ext = filename.split('.')[-1].lower()
            
            if ext not in cls.SUPPORTED_FORMATS:
                raise AudioValidationError(
                    f"Unsupported format: {ext}. "
                    f"Supported: {', '.join(cls.SUPPORTED_FORMATS)}"
                )
            
            # Load audio
            audio_io = io.BytesIO(audio_bytes)
            audio, sr = librosa.load(audio_io, sr=None, mono=True)
            
            # Validate duration
            duration = len(audio) / sr
            if duration < cls.MIN_DURATION:
                raise AudioValidationError(
                    f"Audio too short: {duration:.2f}s. Minimum: {cls.MIN_DURATION}s"
                )
            
            if duration > cls.MAX_DURATION:
                raise AudioValidationError(
                    f"Audio too long: {duration:.2f}s. Maximum: {cls.MAX_DURATION}s"
                )
            
            # Resample if needed
            if sr != cls.TARGET_SAMPLE_RATE:
                audio = librosa.resample(
                    audio,
                    orig_sr=sr,
                    target_sr=cls.TARGET_SAMPLE_RATE,
                )
                sr = cls.TARGET_SAMPLE_RATE
            
            # Validate audio quality
            metadata = cls._assess_quality(audio, sr)
            
            if metadata['snr'] < cls.MIN_SNR:
                raise AudioValidationError(
                    f"Audio SNR too low: {metadata['snr']:.1f}dB. "
                    f"Minimum: {cls.MIN_SNR}dB (too much noise)"
                )
            
            if metadata['loudness'] < cls.MIN_LOUDNESS:
                raise AudioValidationError(
                    f"Audio too quiet: {metadata['loudness']:.1f}LUFS. "
                    f"Minimum: {cls.MIN_LOUDNESS}LUFS"
                )
            
            if metadata['loudness'] > cls.MAX_LOUDNESS:
                raise AudioValidationError(
                    f"Audio too loud: {metadata['loudness']:.1f}LUFS. "
                    f"Maximum: {cls.MAX_LOUDNESS}LUFS (risk of clipping)"
                )
            
            # Normalize audio
            audio = cls._normalize_audio(audio)
            
            logger.info(
                f"Audio validated: {duration:.2f}s @ {sr}Hz, "
                f"SNR={metadata['snr']:.1f}dB, "
                f"Loudness={metadata['loudness']:.1f}LUFS"
            )
            
            return audio, sr, metadata
        
        except AudioValidationError:
            raise
        except Exception as e:
            raise AudioValidationError(f"Audio loading failed: {str(e)}")
    
    @staticmethod
    def _assess_quality(audio: np.ndarray, sr: int) -> dict:
        """Assess audio quality metrics."""
        
        # Calculate loudness (simplified LUFS calculation)
        # In production, use pyloudnorm library
        rms = np.sqrt(np.mean(audio ** 2))
        loudness_db = 20 * np.log10(rms + 1e-10)
        
        # Estimate SNR (signal-to-noise ratio)
        # Simple heuristic: assume first/last 100ms is noise
        noise_duration = int(sr * 0.1)  # 100ms
        noise_samples = np.concatenate([audio[:noise_duration], audio[-noise_duration:]])
        signal_rms = np.sqrt(np.mean(audio ** 2))
        noise_rms = np.sqrt(np.mean(noise_samples ** 2))
        snr = 20 * np.log10(signal_rms / (noise_rms + 1e-10))
        
        # Peak detection
        peak = np.max(np.abs(audio))
        
        # Silence ratio
        silence_threshold = 0.01
        silence_ratio = np.sum(np.abs(audio) < silence_threshold) / len(audio)
        
        return {
            "loudness": loudness_db,
            "snr": snr,
            "peak": peak,
            "silence_ratio": silence_ratio,
            "rms": rms,
        }
    
    @staticmethod
    def _normalize_audio(audio: np.ndarray, target_loudness: float = -20.0) -> np.ndarray:
        """Normalize audio to target loudness."""
        
        # Simple peak normalization first
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * 0.95  # Leave 5% headroom
        
        # In production, use pyloudnorm for proper LUFS normalization
        # For now, return normalized audio
        return audio.astype(np.float32)
    
    @staticmethod
    def get_audio_info(audio: np.ndarray, sr: int) -> dict:
        """Get basic audio information."""
        
        duration = len(audio) / sr
        
        return {
            "duration_seconds": duration,
            "sample_rate": sr,
            "num_samples": len(audio),
            "channels": 1,
            "bit_depth": 32 if audio.dtype == np.float32 else 16,
            "estimated_bitrate": f"{(sr * 32) // 1000}kbps",
        }


# Singleton
_validator = AudioValidator()


def validate_audio(
    audio_bytes: bytes,
    filename: str,
) -> Tuple[np.ndarray, int, dict]:
    """Convenience function."""
    return _validator.validate_audio_file(audio_bytes, filename)
