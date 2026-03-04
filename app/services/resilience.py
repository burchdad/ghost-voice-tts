"""
Error handling and resilience utilities.

Provides:
- Circuit breaker pattern for external service calls
- Graceful degradation
- Retry logic with exponential backoff
- Error telemetry and alerting
"""

import asyncio
import logging
from typing import Callable, Any, Optional, Type
from enum import Enum
from datetime import datetime, timedelta
from functools import wraps

from app.core.metrics import MetricsCollector


logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: Type[Exception] = Exception,
        name: str = "circuit_breaker",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name


class CircuitBreaker:
    """
    Circuit breaker pattern for graceful degradation.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, reject requests
    - HALF_OPEN: Testing if service recovered, limited requests
    
    Example:
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
        try:
            await cb.call(external_service_call)
        except CircuitBreakerOpen:
            # Use fallback
            return fallback_result()
    """
    
    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.success_count = 0
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
            else:
                raise CircuitBreakerOpen(
                    f"Circuit breaker {self.config.name} is OPEN. "
                    f"Retry after {self._retry_after_seconds()}s"
                )
        
        try:
            result = await func(*args, **kwargs) if asyncio.iscoroutinefunction(func) else func(*args, **kwargs)
            self._on_success()
            return result
        except self.config.expected_exception as e:
            self._on_failure()
            raise
    
    def _on_success(self):
        """Record successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= 3:  # 3 successes to close
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                logger.info(f"Circuit breaker {self.config.name} closed (recovered)")
        elif self.state == CircuitState.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)
    
    def _on_failure(self):
        """Record failed call."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        
        if self.failure_count >= self.config.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                f"Circuit breaker {self.config.name} opened. "
                f"Failure count: {self.failure_count}"
            )
            MetricsCollector.record_circuit_breaker_open(self.config.name)
        
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning(f"Circuit breaker {self.config.name} reopened (still failing)")
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to try recovery."""
        if not self.last_failure_time:
            return False
        
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.config.recovery_timeout
    
    def _retry_after_seconds(self) -> int:
        """Calculate seconds until retry can be attempted."""
        if not self.last_failure_time:
            return self.config.recovery_timeout
        
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return max(0, int(self.config.recovery_timeout - elapsed))
    
    def reset(self):
        """Manually reset circuit breaker."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.success_count = 0


class RetryConfig:
    """Configuration for retry logic."""
    
    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


async def retry_with_backoff(
    func: Callable,
    config: RetryConfig = None,
    on_retry: Optional[Callable] = None,
) -> Any:
    """
    Execute function with exponential backoff retry.
    
    Args:
        func: Async function to execute
        config: Retry configuration
        on_retry: Callback on retry attempt
    
    Returns:
        Result of successful function call
    
    Raises:
        Last exception if max retries exceeded
    """
    if config is None:
        config = RetryConfig()
    
    last_exception = None
    
    for attempt in range(config.max_retries + 1):
        try:
            return await func() if asyncio.iscoroutinefunction(func) else func()
        except Exception as e:
            last_exception = e
            
            if attempt == config.max_retries:
                logger.error(f"All {config.max_retries} retries exhausted: {e}")
                raise
            
            # Calculate backoff
            delay = min(
                config.initial_delay * (config.exponential_base ** attempt),
                config.max_delay,
            )
            
            if config.jitter:
                import random
                delay *= random.uniform(0.5, 1.0)
            
            logger.warning(
                f"Attempt {attempt + 1} failed: {e}. "
                f"Retrying in {delay:.1f}s..."
            )
            
            if on_retry:
                on_retry(attempt + 1, delay, e)
            
            await asyncio.sleep(delay)
    
    raise last_exception


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open."""
    pass


class TimeoutError(Exception):
    """Raised when operation times out."""
    pass


async def timeout(coro, seconds: float):
    """Execute coroutine with timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Operation timed out after {seconds}s")


class ErrorTelemetry:
    """Collect and report error metrics."""
    
    @staticmethod
    def record_error(
        error_type: str,
        error_message: str,
        endpoint: str,
        user_id: Optional[str] = None,
        severity: str = "error",
    ):
        """Record an error occurrence."""
        MetricsCollector.record_error(error_type, severity)
        
        logger.log(
            logging.ERROR if severity == "error" else logging.WARNING,
            f"[{error_type}] {error_message}",
            extra={
                "endpoint": endpoint,
                "user_id": user_id,
                "severity": severity,
            },
        )
    
    @staticmethod
    def record_error_recovery(service_name: str):
        """Record recovery from error state."""
        logger.info(f"Service {service_name} recovered from error state")
        MetricsCollector.record_metric(f"recovery:{service_name}", 1)


# Global circuit breakers for common services
_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str, config: CircuitBreakerConfig = None) -> CircuitBreaker:
    """Get or create circuit breaker for service."""
    if name not in _circuit_breakers:
        if config is None:
            config = CircuitBreakerConfig(name=name)
        _circuit_breakers[name] = CircuitBreaker(config)
    return _circuit_breakers[name]
