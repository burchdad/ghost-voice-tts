"""
Provider routing layer for Ghost Voice TTS.

Responsibilities
----------------
* Maintain per-provider health state (healthy / degraded / unhealthy).
* Route synthesis requests to the best provider for a given latency tier.
* Wrap each provider call in its own CircuitBreaker.
* Expose health snapshots for the capability descriptor and admin dashboard.
* **Adaptive learning** — track rolling latency + quality per provider and
  automatically adjust routing weights so the system self-optimizes over time.

Tier → provider preference order
---------------------------------
  realtime     → vits → elevenlabs → tortoise
  balanced     → auto (pick healthiest, prefer vits then tortoise)
  high_quality → tortoise → elevenlabs → vits

Adaptive routing
-----------------
Each provider maintains an exponential-moving-average (EMA) latency and a
quality score derived from its recent success rate.  ``select_provider()``
uses these scores to compute a weighted random selection when multiple
providers are healthy — the best performer gets proportionally more traffic
rather than always being chosen first.

The EMA alpha (smoothing factor) is configurable via
``PROVIDER_LEARNING_ALPHA`` (default 0.15 — roughly a 12-sample window).
Set to 1.0 to disable smoothing (last-observed value only).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from app.core.config import get_settings
from app.services.resilience import CircuitBreaker, CircuitBreakerConfig

logger = logging.getLogger(__name__)
settings = get_settings()

# EMA smoothing factor for latency and quality score adaptation.
# Lower = slower to adapt (more stable); higher = reacts faster to changes.
_EMA_ALPHA: float = getattr(settings, "PROVIDER_LEARNING_ALPHA", 0.15)

# Minimum routing weight so a degraded provider can still receive some
# traffic and recover naturally (prevents permanent starvation).
_MIN_WEIGHT: float = 0.05


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class ProviderHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ProviderState:
    name: str
    health: ProviderHealth = ProviderHealth.HEALTHY
    failure_count: int = 0
    success_count: int = 0
    last_failure_at: Optional[float] = None
    last_success_at: Optional[float] = None

    # ── Adaptive learning fields ──────────────────────────────────────────
    # EMA latency in milliseconds.  Initialised to a neutral value so all
    # providers start equal and diverge as real observations arrive.
    ema_latency_ms: float = 500.0

    # Quality score in [0, 1].  1.0 = perfect success rate.
    quality_score: float = 1.0

    # Computed routing weight; recalculated on every record_success/failure.
    routing_weight: float = 1.0

    # Legacy field kept for compatibility with external health snapshots.
    avg_latency_ms: float = 0.0
    _latency_samples: List[float] = field(default_factory=list, repr=False)

    # Tier preferences (lower = more preferred)
    tier_priority: Dict[str, int] = field(default_factory=dict, repr=False)

    def record_success(self, latency_ms: float) -> None:
        self.failure_count = 0
        self.success_count += 1
        self.last_success_at = time.monotonic()
        self.health = ProviderHealth.HEALTHY

        # EMA latency update
        self.ema_latency_ms = (
            _EMA_ALPHA * latency_ms + (1 - _EMA_ALPHA) * self.ema_latency_ms
        )

        # Quality score: EMA of binary success signal (1.0 = success)
        self.quality_score = min(
            1.0, _EMA_ALPHA * 1.0 + (1 - _EMA_ALPHA) * self.quality_score
        )

        # Legacy avg_latency_ms kept for old callers
        self._latency_samples.append(latency_ms)
        if len(self._latency_samples) > 20:
            self._latency_samples = self._latency_samples[-20:]
        self.avg_latency_ms = sum(self._latency_samples) / len(self._latency_samples)

        self._recompute_weight()

    def record_failure(self, threshold: int, recovery_timeout: int) -> None:
        self.failure_count += 1
        self.last_failure_at = time.monotonic()

        # Quality score: EMA of binary failure signal (0.0 = failure)
        self.quality_score = max(
            0.0, _EMA_ALPHA * 0.0 + (1 - _EMA_ALPHA) * self.quality_score
        )

        if self.failure_count >= threshold:
            self.health = ProviderHealth.UNHEALTHY
            logger.warning(
                "Provider %s marked UNHEALTHY after %d failures",
                self.name,
                self.failure_count,
            )
        elif self.failure_count >= max(1, threshold // 2):
            self.health = ProviderHealth.DEGRADED

        self._recompute_weight()

    def _recompute_weight(self) -> None:
        """
        Derive a routing weight from quality score and EMA latency.

        Formula:  weight = quality_score / (ema_latency_ms ^ 0.5)

        This favours low-latency, high-quality providers.  The sqrt dampens
        the latency effect so a 2× faster provider doesn't get 4× the traffic.
        The minimum weight prevents complete starvation.
        """
        latency_factor = max(1.0, self.ema_latency_ms) ** 0.5
        raw = self.quality_score / latency_factor
        self.routing_weight = max(_MIN_WEIGHT, raw)

    def maybe_recover(self, recovery_timeout: int) -> bool:
        """Return True (and mark healthy) if the recovery window has elapsed."""
        if self.health == ProviderHealth.HEALTHY:
            return True
        if self.last_failure_at is None:
            return False
        if (time.monotonic() - self.last_failure_at) >= recovery_timeout:
            logger.info("Provider %s recovered, marking HEALTHY", self.name)
            self.health = ProviderHealth.HEALTHY
            self.failure_count = 0
            self._recompute_weight()
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "health": self.health,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "ema_latency_ms": round(self.ema_latency_ms, 1),
            "quality_score": round(self.quality_score, 3),
            "routing_weight": round(self.routing_weight, 4),
        }


# ---------------------------------------------------------------------------
# Tier → ordered provider preference
# ---------------------------------------------------------------------------

# Base preference tables (overridden at runtime by deployment profile).
_TIER_PREFERENCES: Dict[str, List[str]] = {
    "realtime":     ["vits", "elevenlabs", "tortoise"],
    "balanced":     ["vits", "tortoise", "elevenlabs"],
    "high_quality": ["tortoise", "elevenlabs", "vits"],
}


def _build_preferences(profile: str) -> Dict[str, List[str]]:
    """
    Return a tier-preference table adjusted for the deployment profile.

    cloud   — no change (default order)
    edge    — promote edge providers to the top of every tier
    hybrid  — realtime/balanced favour edge; high_quality favours cloud
    """
    if profile == "cloud":
        return dict(_TIER_PREFERENCES)

    edge = list(settings.EDGE_PROVIDERS)
    cloud = list(settings.CLOUD_PROVIDERS)
    # Providers not in either list (shouldn't normally happen)
    all_known = ["vits", "tortoise", "elevenlabs"]
    other = [p for p in all_known if p not in edge and p not in cloud]

    if profile == "edge":
        # All tiers prefer edge first
        preferred_order = edge + cloud + other
        return {k: preferred_order for k in _TIER_PREFERENCES}

    # hybrid
    return {
        "realtime":     edge + cloud + other,
        "balanced":     edge + cloud + other,
        "high_quality": cloud + edge + other,
    }


# ---------------------------------------------------------------------------
# ProviderRouter
# ---------------------------------------------------------------------------

class ProviderRouter:
    """
    Singleton-like router; call ``get_provider_router()`` for the shared
    instance rather than constructing directly.
    """

    def __init__(self) -> None:
        self._providers: Dict[str, ProviderState] = {}
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
        # Build preference table from deployment profile
        self._preferences: Dict[str, List[str]] = _build_preferences(
            settings.DEPLOYMENT_PROFILE
        )

        for name in ("vits", "tortoise", "elevenlabs"):
            self._providers[name] = ProviderState(name=name)
            self._circuit_breakers[name] = CircuitBreaker(
                CircuitBreakerConfig(
                    name=f"provider:{name}",
                    failure_threshold=settings.PROVIDER_FAILURE_THRESHOLD,
                    recovery_timeout=settings.PROVIDER_RECOVERY_TIMEOUT,
                )
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_provider(self, mode: str) -> str:
        """
        Return the name of the best available provider for *mode*.

        Selection strategy
        ------------------
        1. Allow recovery windows to flip unhealthy providers back first.
        2. If a config override is set for this tier and that provider is
           healthy, use it unconditionally.
        3. Otherwise, collect all healthy providers in preference order and
           perform a **weighted random selection** using ``routing_weight``
           (derived from EMA latency and quality score).  This means the
           system routes *more* traffic to the fastest, most-reliable
           provider without completely starving the others.
        4. If all providers are unhealthy, fall back to the first in the
           preference list (better to try than to fail fast).
        """
        # Allow recovery windows to flip state before we decide.
        for state in self._providers.values():
            state.maybe_recover(settings.PROVIDER_RECOVERY_TIMEOUT)

        # Config override for specific tiers.
        cfg_override = {
            "realtime":     settings.PROVIDER_REALTIME,
            "balanced":     settings.PROVIDER_BALANCED,
            "high_quality": settings.PROVIDER_HIGH_QUALITY,
        }.get(mode, "auto")

        if cfg_override != "auto" and cfg_override in self._providers:
            state = self._providers[cfg_override]
            if state.health != ProviderHealth.UNHEALTHY:
                return cfg_override

        preferences = self._preferences.get(mode, self._preferences.get("balanced", ["vits", "tortoise", "elevenlabs"]))

        # Collect healthy candidates with their adaptive weights.
        candidates = [
            (name, self._providers[name].routing_weight)
            for name in preferences
            if name in self._providers
            and self._providers[name].health != ProviderHealth.UNHEALTHY
        ]

        if not candidates:
            # All providers unhealthy — use first preference as last resort.
            return preferences[0]

        if len(candidates) == 1:
            return candidates[0][0]

        # Weighted random selection — biases traffic toward top performers
        # while still allowing others to serve requests and recover.
        names, weights = zip(*candidates)
        total = sum(weights)
        normalised = [w / total for w in weights]
        chosen = random.choices(names, weights=normalised, k=1)[0]
        logger.debug(
            "Provider selected: %s (mode=%s, weights=%s)",
            chosen, mode,
            {n: round(w, 3) for n, w in zip(names, normalised)},
        )
        return chosen

    async def call(
        self,
        mode: str,
        func: Callable,
        *args,
        **kwargs,
    ):
        """
        Route a synthesis call through the circuit breaker of the selected
        provider, recording success / failure for health tracking.

        ``func`` receives ``provider_name`` as a keyword argument so the TTS
        engine can branch on it.
        """
        provider_name = self.select_provider(mode)
        state = self._providers[provider_name]
        cb = self._circuit_breakers[provider_name]

        start = time.monotonic()
        try:
            result = await cb.call(func, *args, provider_name=provider_name, **kwargs)
            latency_ms = (time.monotonic() - start) * 1000
            state.record_success(latency_ms)
            logger.debug(
                "Provider %s succeeded in %.1f ms (mode=%s)",
                provider_name, latency_ms, mode,
            )
            return result, provider_name
        except Exception as exc:
            state.record_failure(
                settings.PROVIDER_FAILURE_THRESHOLD,
                settings.PROVIDER_RECOVERY_TIMEOUT,
            )
            logger.warning(
                "Provider %s failed (mode=%s): %s — trying fallback",
                provider_name, mode, exc,
            )
            # Try next healthy provider in preference order
            preferences = self._preferences.get(mode, self._preferences.get("balanced", []))
            for fallback in preferences:
                if fallback == provider_name:
                    continue
                fb_state = self._providers.get(fallback)
                if fb_state and fb_state.health != ProviderHealth.UNHEALTHY:
                    try:
                        fb_cb = self._circuit_breakers[fallback]
                        fb_start = time.monotonic()
                        result = await fb_cb.call(
                            func, *args, provider_name=fallback, **kwargs
                        )
                        fb_state.record_success((time.monotonic() - fb_start) * 1000)
                        return result, fallback
                    except Exception:
                        fb_state.record_failure(
                            settings.PROVIDER_FAILURE_THRESHOLD,
                            settings.PROVIDER_RECOVERY_TIMEOUT,
                        )
            raise exc  # all providers exhausted

    def health_snapshot(self) -> Dict[str, dict]:
        """Return {provider_name: health_dict} for observability."""
        return {name: state.to_dict() for name, state in self._providers.items()}

    def deployment_info(self) -> dict:
        """Return deployment profile metadata for the capability descriptor."""
        return {
            "profile": settings.DEPLOYMENT_PROFILE,
            "edge_providers": list(settings.EDGE_PROVIDERS),
            "cloud_providers": list(settings.CLOUD_PROVIDERS),
            "edge_latency_budget_ms": settings.EDGE_LATENCY_BUDGET_MS,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_router: Optional[ProviderRouter] = None


def get_provider_router() -> ProviderRouter:
    global _router
    if _router is None:
        _router = ProviderRouter()
    return _router
