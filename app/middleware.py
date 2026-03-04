"""
FastAPI middleware for security, rate limiting, and observability.
"""

import time
import logging
import uuid
from typing import Callable

from fastapi import Request, status, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.metrics import MetricsCollector
from app.services.rate_limiter import get_rate_limiter, RateLimitTier
from app.services.security import get_security_manager, TokenData

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware.
    
    Checks per-user and per-IP rate limits before processing requests.
    Returns 429 (Too Many Requests) when limits exceeded.
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Callable:
        """Process request with rate limiting."""
        # Skip rate limiting for health checks and metrics
        if request.url.path in ["/health", "/prometheus/metrics", "/metrics", "/docs", "/openapi.json"]:
            return await call_next(request)
        
        rate_limiter = get_rate_limiter()
        client_ip = request.client.host if request.client else "unknown"
        
        # Check IP-level rate limiting (abuse prevention)
        ip_allowed, ip_remaining, ip_reset = await rate_limiter.check_ip_limit(
            client_ip,
            cost=1,
        )
        
        if not ip_allowed:
            MetricsCollector.record_rate_limit_exceeded("ip", request.url.path)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": "Too many requests from this IP",
                    "retry_after": ip_reset,
                },
            )
        
        # Try to get user from token
        user_tier = RateLimitTier.FREE
        user_id = client_ip  # Fallback to IP if no user
        
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            security_mgr = get_security_manager()
            token_data = security_mgr.verify_token(token)
            
            if token_data:
                user_id = token_data.user_id
                user_tier = RateLimitTier(token_data.tier)
        
        # Check user-level rate limiting
        allowed, remaining, reset = await rate_limiter.check_endpoint_limit(
            user_id=user_id,
            tier=user_tier,
            endpoint=request.url.path,
            cost=1,
        )
        
        if not allowed:
            MetricsCollector.record_rate_limit_exceeded(user_tier.value, request.url.path)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": f"Rate limit exceeded. Remaining requests: {remaining}. Reset in {reset}s",
                    "retry_after": reset,
                    "remaining": remaining,
                },
            )
        
        # Add rate limit headers to response
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset)
        response.headers["X-Process-Time"] = str(process_time)
        
        # Record metrics
        MetricsCollector.record_request_latency(request.url.path, request.method, process_time)
        MetricsCollector.record_http_request(request.method, request.url.path, response.status_code)
        
        return response


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """
    Error handling middleware.
    
    Catches exceptions and returns proper error responses.
    Records error metrics and logging.
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Callable:
        """Process request with error handling."""
        try:
            response = await call_next(request)
            return response
        except HTTPException as e:
            # FastAPI HTTP exceptions
            MetricsCollector.record_error(
                error_type="http_exception",
                severity="warning" if 400 <= e.status_code < 500 else "error",
            )
            raise
        except ValueError as e:
            # Validation errors
            MetricsCollector.record_error("validation_error")
            logger.warning(f"Validation error: {e}")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": str(e)},
            )
        except KeyError as e:
            # Missing keys
            MetricsCollector.record_error("key_error")
            logger.warning(f"Key error: {e}")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": f"Missing required field: {e}"},
            )
        except RuntimeError as e:
            # Runtime errors (temp failures, etc)
            MetricsCollector.record_error("runtime_error")
            logger.error(f"Runtime error: {e}")
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "Service temporarily unavailable"},
            )
        except Exception as e:
            # Catch-all for unexpected errors
            MetricsCollector.record_error("unexpected_error", severity="error")
            logger.exception(f"Unexpected error: {e}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Internal server error"},
            )


class RequestTrackingMiddleware(BaseHTTPMiddleware):
    """
    Request tracking middleware.
    
    Adds request ID, tracks concurrent requests, and logs request details.
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Callable:
        """Track request processing."""
        import uuid
        
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        # Track concurrent requests
        MetricsCollector.increment_concurrent_requests(request.url.path)
        
        start_time = time.time()
        try:
            response = await call_next(request)
            duration = time.time() - start_time
            
            # Log request
            logger.info(
                f"[{request_id}] {request.method} {request.url.path} "
                f"-> {response.status_code} ({duration:.3f}s)"
            )
            
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            MetricsCollector.decrement_concurrent_requests(request.url.path)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to responses.
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Callable:
        """Add security headers."""
        response = await call_next(request)
        
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        
        return response


def setup_middlewares(app: FastAPI):
    """Add all middleware to FastAPI app."""
    # Order matters: innermost middleware processes request first
    # 1. Security headers (should be last to modify response)
    app.add_middleware(SecurityHeadersMiddleware)
    
    # 2. Request tracking (track all requests)
    app.add_middleware(RequestTrackingMiddleware)
    
    # 3. Error handling (catch errors)
    app.add_middleware(ErrorHandlingMiddleware)
    
    # 4. Rate limiting (decide if request should proceed)
    app.add_middleware(RateLimitMiddleware)
