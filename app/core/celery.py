from celery import Celery, Task
from kombu import Exchange, Queue
import logging

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class ContextTask(Task):
    """Make celery tasks work with sqlmodel context."""
    
    def __call__(self, *args, **kwargs):
        # Add any context setup here if needed
        return self.run(*args, **kwargs)


# Initialize Celery app
celery_app = Celery(
    "ghost_voice_tts",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND_URL,
)

# Configure Celery
celery_app.conf.update(
    task_serializer=settings.CELERY_TASK_SERIALIZER,
    accept_content=["json"],
    result_serializer=settings.CELERY_RESULT_SERIALIZER,
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes hard limit
    task_soft_time_limit=25 * 60,  # 25 minutes soft limit
    worker_prefetch_multiplier=4,
    worker_max_tasks_per_child=1000,
    result_expires=3600,  # Results expire after 1 hour
)

# ── Priority-tiered queues ────────────────────────────────────────────────────
# Three synthesis queues map to the three latency tiers:
#   tts.realtime     – highest priority (x_max_priority=10, default priority=8)
#   tts.balanced     – normal throughput (priority=5)
#   tts.high_quality – background batch  (priority=2)
#
# Workers should be started with --queues tts.realtime,tts.balanced,tts.high_quality
# Redis broker supports priority via x-max-priority.

synthesis_exchange = Exchange("tts", type="direct")

celery_app.conf.task_queues = (
    Queue(
        "tts.realtime",
        synthesis_exchange,
        routing_key="tts.realtime",
        queue_arguments={"x-max-priority": 10},
    ),
    Queue(
        "tts.balanced",
        synthesis_exchange,
        routing_key="tts.balanced",
        queue_arguments={"x-max-priority": 10},
    ),
    Queue(
        "tts.high_quality",
        synthesis_exchange,
        routing_key="tts.high_quality",
        queue_arguments={"x-max-priority": 10},
    ),
    # Legacy / voice-cloning queues kept for backward compat
    Queue("synthesis", synthesis_exchange, routing_key="synthesis"),
    Queue("voice_cloning", synthesis_exchange, routing_key="voice_cloning"),
    Queue("default", synthesis_exchange, routing_key="default"),
)

# Default route (mode-aware routing happens in the endpoint via apply_async)
celery_app.conf.task_routes = {
    "app.tasks.synthesis.synthesize_text": {"queue": "tts.balanced"},
    "app.tasks.voice_cloning.encode_voice_samples": {"queue": "voice_cloning"},
}

# ── Priority helpers ──────────────────────────────────────────────────────────
_MODE_QUEUE: dict = {
    "realtime":     ("tts.realtime",     8),
    "balanced":     ("tts.balanced",     5),
    "high_quality": ("tts.high_quality", 2),
}


def queue_for_mode(mode: str) -> tuple[str, int]:
    """Return (queue_name, priority) for a synthesis mode string."""
    return _MODE_QUEUE.get(mode, _MODE_QUEUE["balanced"])


celery_app.Task = ContextTask

logger.info(f"Celery initialized with broker: {settings.CELERY_BROKER_URL}")
