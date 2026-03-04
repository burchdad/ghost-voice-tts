"""
Rate limiting service using token bucket algorithm.

Implements per-user and per-IP rate limiting for API endpoints.
Tracks usage in Redis for distributed rate limiting.
"""

import time
from typing import Tuple, Optional
from enum import Enum

from app.core.cache import RedisCache
from app.core.config import settings


class RateLimitTier(str, Enum):
    """Rate limit tiers based on user subscription level."""
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    ADMIN = "admin"


class RateLimitConfig:
    """Configuration for rate limits per tier."""
    
    # Requests per minute per endpoint
    ENDPOINT_LIMITS = {
        "free": 30,
        "starter": 100,
        "pro": 500,
        "enterprise": 5000,
        "admin": 10000,
    }
    
    # Characters per minute per tier (for synthesis)
    CHARACTER_LIMITS = {
        "free": 5_000,        # ~1 page of text
        "starter": 50_000,    # ~10 pages
        "pro": 500_000,       # ~100 pages
        "enterprise": 5_000_000,  # ~1000 pages
        "admin": float('inf'),
    }
    
    # IP-level rate limiting (shared across all users on IP)
    IP_REQUESTS_PER_MINUTE = 1000
    IP_BURST_SIZE = 2000
    
    # Per-endpoint overrides
    ENDPOINT_OVERRIDES = {
        "/synthesize": {"free": 10, "starter": 50, "pro": 200, "enterprise": 1000},
        "/synthesize-batch": {"free": 5, "starter": 20, "pro": 100, "enterprise": 500},
        "/voices/create": {"free": 2, "starter": 10, "pro": 50, "enterprise": 200},
    }


class RateLimiter:
    """
    Token bucket rate limiter.
    
    Tracks tokens in Redis. Each request costs tokens.
    Tokens refill at configurable rate.
    Supports burst capacity.
    """
    
    def __init__(self, cache: RedisCache):
        self.cache = cache
        self.config = RateLimitConfig()
    
    async def check_endpoint_limit(
        self,
        user_id: str,
        tier: RateLimitTier,
        endpoint: str,
        cost: int = 1,
    ) -> Tuple[bool, int, int]:
        """
        Check if user can make request to endpoint.
        
        Args:
            user_id: User ID
            tier: User subscription tier
            endpoint: API endpoint path (e.g., "/synthesize")
            cost: Token cost of this request (default 1)
        
        Returns:
            (allowed: bool, remaining_tokens: int, reset_after_seconds: int)
        """
        key = f"rate_limit:endpoint:{user_id}:{endpoint}"
        limit = self._get_endpoint_limit(endpoint, tier)
        
        allowed, remaining, reset = await self._check_bucket(
            key=key,
            capacity=limit,
            refill_rate=limit / 60,  # Refill over 1 minute
            cost=cost,
        )
        
        return allowed, remaining, reset
    
    async def check_character_limit(
        self,
        user_id: str,
        tier: RateLimitTier,
        character_count: int,
    ) -> Tuple[bool, int, int]:
        """
        Check if user can synthesize character_count characters this minute.
        
        Args:
            user_id: User ID
            tier: User subscription tier
            character_count: Number of characters to synthesize
        
        Returns:
            (allowed: bool, remaining_chars: int, reset_after_seconds: int)
        """
        key = f"rate_limit:chars:{user_id}"
        limit = self.config.CHARACTER_LIMITS[tier.value]
        
        if limit == float('inf'):
            return True, int(limit), 0
        
        allowed, remaining, reset = await self._check_bucket(
            key=key,
            capacity=int(limit),
            refill_rate=int(limit) / 60,  # Refill over 1 minute
            cost=character_count,
        )
        
        return allowed, remaining, reset
    
    async def check_ip_limit(
        self,
        ip_address: str,
        cost: int = 1,
    ) -> Tuple[bool, int, int]:
        """
        Check if IP address can make request (global abuse protection).
        
        Args:
            ip_address: Client IP address
            cost: Token cost of this request
        
        Returns:
            (allowed: bool, remaining_tokens: int, reset_after_seconds: int)
        """
        key = f"rate_limit:ip:{ip_address}"
        
        allowed, remaining, reset = await self._check_bucket(
            key=key,
            capacity=self.config.IP_BURST_SIZE,
            refill_rate=self.config.IP_REQUESTS_PER_MINUTE / 60,
            cost=cost,
        )
        
        return allowed, remaining, reset
    
    async def _check_bucket(
        self,
        key: str,
        capacity: int,
        refill_rate: float,
        cost: int,
    ) -> Tuple[bool, int, int]:
        """
        Token bucket algorithm.
        
        Args:
            key: Redis key for this bucket
            capacity: Max tokens in bucket
            refill_rate: Tokens per second
            cost: Tokens to consume
        
        Returns:
            (allowed: bool, remaining_tokens: int, reset_after_seconds: int)
        """
        now = time.time()
        
        # Get current bucket state
        bucket_data = await self.cache.client.hgetall(key)
        
        if not bucket_data:
            # New bucket: fill it
            tokens = capacity
            last_refill = now
        else:
            tokens = float(bucket_data.get(b'tokens', capacity))
            last_refill = float(bucket_data.get(b'last_refill', now))
        
        # Calculate refilled tokens since last check
        elapsed = max(0, now - last_refill)
        refilled = elapsed * refill_rate
        tokens = min(capacity, tokens + refilled)
        
        # Check if we have enough tokens
        if tokens >= cost:
            tokens -= cost
            allowed = True
        else:
            allowed = False
        
        # Store updated bucket state
        await self.cache.client.hset(
            key,
            mapping={
                b'tokens': str(tokens),
                b'last_refill': str(now),
            },
        )
        
        # Set expiration (clean up old buckets)
        await self.cache.client.expire(key, 3600)
        
        # Calculate reset time (when bucket will be full)
        if tokens < capacity:
            tokens_needed = capacity - tokens
            seconds_to_reset = tokens_needed / refill_rate if refill_rate > 0 else 0
        else:
            seconds_to_reset = 0
        
        return allowed, int(tokens), int(seconds_to_reset)
    
    def _get_endpoint_limit(self, endpoint: str, tier: RateLimitTier) -> int:
        """Get rate limit for specific endpoint and tier."""
        if endpoint in self.config.ENDPOINT_OVERRIDES:
            return self.config.ENDPOINT_OVERRIDES[endpoint][tier.value]
        return self.config.ENDPOINT_LIMITS[tier.value]
    
    async def get_limits_info(
        self,
        user_id: str,
        tier: RateLimitTier,
    ) -> dict:
        """Get current rate limit status for user."""
        return {
            "tier": tier.value,
            "endpoint_limit_per_min": self.config.ENDPOINT_LIMITS[tier.value],
            "character_limit_per_min": self.config.CHARACTER_LIMITS[tier.value],
            "endpoints": {
                name: limit[tier.value]
                for name, limit in self.config.ENDPOINT_OVERRIDES.items()
            },
        }
    
    async def reset_limits(self, user_id: str) -> None:
        """Reset all rate limits for user (admin function)."""
        # Find all keys for this user
        cursor = 0
        pattern = f"rate_limit:*:{user_id}:*"
        
        while True:
            cursor, keys = await self.cache.client.scan(cursor, match=pattern)
            for key in keys:
                await self.cache.client.delete(key)
            if cursor == 0:
                break


# Singleton instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        from app.core.cache import get_redis_cache
        cache = get_redis_cache()
        _rate_limiter = RateLimiter(cache)
    return _rate_limiter
