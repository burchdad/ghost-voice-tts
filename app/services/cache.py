import redis
import json
import logging
from typing import Optional, Any
from datetime import timedelta

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class RedisCache:
    """Redis caching layer for synthesis results and voice embeddings."""
    
    def __init__(self):
        self.pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            decode_responses=False,
        )
        self.client = redis.Redis(connection_pool=self.pool)
        self.ttl = settings.TTS_CACHE_TTL
    
    def health_check(self) -> bool:
        """Check if Redis is available."""
        try:
            self.client.ping()
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False
    
    def get_audio(self, cache_key: str) -> Optional[bytes]:
        """Retrieve cached audio."""
        try:
            data = self.client.get(cache_key)
            if data:
                logger.debug(f"Cache hit for key: {cache_key}")
                return data
            return None
        except Exception as e:
            logger.error(f"Cache get failed: {e}")
            return None
    
    def set_audio(self, cache_key: str, audio_data: bytes, ttl: Optional[int] = None) -> bool:
        """Store audio in cache."""
        try:
            ttl = ttl or self.ttl
            self.client.setex(cache_key, ttl, audio_data)
            logger.debug(f"Cached audio with key: {cache_key} (TTL: {ttl}s)")
            return True
        except Exception as e:
            logger.error(f"Cache set failed: {e}")
            return False
    
    def get_embedding(self, voice_id: str) -> Optional[bytes]:
        """Retrieve speaker embedding."""
        return self.get(f"embedding:{voice_id}")
    
    def set_embedding(self, voice_id: str, embedding: bytes) -> bool:
        """Store speaker embedding with longer TTL."""
        return self.set(f"embedding:{voice_id}", embedding, ttl=86400)  # 24 hours
    
    def get_job_status(self, job_id: str) -> Optional[dict]:
        """Get synthesis job status from cache."""
        try:
            data = self.client.get(f"job:{job_id}")
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Failed to get job status: {e}")
            return None
    
    def set_job_status(self, job_id: str, status: dict, ttl: int = 3600) -> bool:
        """Store synthesis job status."""
        try:
            self.client.setex(
                f"job:{job_id}",
                ttl,
                json.dumps(status)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to set job status: {e}")
            return False
    
    def increment_counter(self, key: str, amount: int = 1) -> int:
        """Increment a counter."""
        try:
            return self.client.incrby(key, amount)
        except Exception as e:
            logger.error(f"Failed to increment counter: {e}")
            return 0
    
    def get_counter(self, key: str) -> int:
        """Get counter value."""
        try:
            val = self.client.get(key)
            return int(val) if val else 0
        except Exception as e:
            logger.error(f"Failed to get counter: {e}")
            return 0
    
    def delete(self, key: str) -> bool:
        """Delete a key."""
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Failed to delete key: {e}")
            return False
    
    def clear_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern."""
        try:
            cursor = 0
            deleted = 0
            while True:
                cursor, keys = self.client.scan(cursor, match=pattern, count=100)
                if keys:
                    deleted += self.client.delete(*keys)
                if cursor == 0:
                    break
            return deleted
        except Exception as e:
            logger.error(f"Failed to clear pattern: {e}")
            return 0
    
    def get(self, key: str) -> Optional[bytes]:
        """Generic get."""
        try:
            return self.client.get(key)
        except Exception as e:
            logger.error(f"Cache get failed: {e}")
            return None
    
    def set(self, key: str, value: bytes, ttl: Optional[int] = None) -> bool:
        """Generic set."""
        try:
            ttl = ttl or self.ttl
            self.client.setex(key, ttl, value)
            return True
        except Exception as e:
            logger.error(f"Cache set failed: {e}")
            return False
    
    def close(self):
        """Close Redis connection pool."""
        self.pool.disconnect()


# Singleton instance
_redis_cache: Optional[RedisCache] = None


def get_redis_cache() -> RedisCache:
    """Get or create Redis cache instance."""
    global _redis_cache
    if _redis_cache is None:
        _redis_cache = RedisCache()
    return _redis_cache
