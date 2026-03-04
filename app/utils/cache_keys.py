import hashlib
from typing import Dict, Any


class CacheKeyGenerator:
    """Generate consistent cache keys for TTS synthesis results."""
    
    # Versioning for cache invalidation
    MODEL_VERSION = "v1.0"
    CACHE_VERSION = "v1"
    
    @staticmethod
    def generate_synthesis_key(
        text: str,
        voice_id: str,
        language: str = "en",
        style: str = "normal",
        speed: float = 1.0,
        pitch: float = 1.0,
        model: str = "tortoise",
    ) -> str:
        """
        Generate a deterministic cache key for synthesis results.
        
        Includes all parameters that affect output:
        - text content
        - voice identity
        - language
        - speech style
        - speed and pitch modifications
        - model version
        
        This ensures:
        - Same inputs with same voice always hit cache
        - Different parameters don't collide
        - Model updates invalidate old cache entries
        """
        
        key_parts = {
            "model": model,
            "model_version": CacheKeyGenerator.MODEL_VERSION,
            "text": text.strip().lower(),
            "voice_id": voice_id,
            "language": language,
            "style": style,
            "speed": round(speed, 2),  # Round to avoid floating point issues
            "pitch": round(pitch, 2),
            "cache_version": CacheKeyGenerator.CACHE_VERSION,
        }
        
        # Create deterministic string representation
        key_str = "|".join(f"{k}={v}" for k, v in sorted(key_parts.items()))
        
        # Hash to keep key size reasonable
        hash_value = hashlib.sha256(key_str.encode()).hexdigest()
        
        return f"tts_audio:{hash_value}"
    
    @staticmethod
    def generate_embedding_key(
        voice_id: str,
        model: str = "resemblyzer",
    ) -> str:
        """Generate cache key for voice embeddings."""
        
        key_parts = {
            "type": "voice_embedding",
            "voice_id": voice_id,
            "model": model,
            "version": CacheKeyGenerator.MODEL_VERSION,
        }
        
        key_str = "|".join(f"{k}={v}" for k, v in sorted(key_parts.items()))
        hash_value = hashlib.sha256(key_str.encode()).hexdigest()
        
        return f"tts_embedding:{hash_value}"
    
    @staticmethod
    def generate_job_key(job_id: str) -> str:
        """Generate cache key for synthesis job status."""
        return f"tts_job:{job_id}"
    
    @staticmethod
    def invalidate_voice_cache(voice_id: str) -> str:
        """
        Generate pattern to invalidate all cache entries for a voice.
        
        Useful when voice is updated or retrained.
        """
        return f"tts_audio:*voice_id={voice_id}*"
    
    @staticmethod
    def invalidate_model_cache(model: str) -> str:
        """Invalidate all cache when model is updated."""
        return f"tts_audio:*model={model}*"


# Utility function
def get_synthesis_cache_key(
    text: str,
    voice_id: str,
    **kwargs
) -> str:
    """Convenience function to generate synthesis cache key."""
    return CacheKeyGenerator.generate_synthesis_key(text, voice_id, **kwargs)


def get_embedding_cache_key(voice_id: str, model: str = "resemblyzer") -> str:
    """Convenience function to generate embedding cache key."""
    return CacheKeyGenerator.generate_embedding_key(voice_id, model)
