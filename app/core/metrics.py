import logging
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry
import time

# Create a registry for metrics
metrics_registry = CollectorRegistry()

# ============ Counters ============

synthesis_requests_total = Counter(
    "tts_synthesis_requests_total",
    "Total number of synthesis requests",
    ["status", "model"],
    registry=metrics_registry,
)

synthesis_failures_total = Counter(
    "tts_synthesis_failures_total",
    "Total number of failed synthesis requests",
    ["error_type"],
    registry=metrics_registry,
)

cache_hits_total = Counter(
    "tts_cache_hits_total",
    "Total number of cache hits",
    ["cache_type"],
    registry=metrics_registry,
)

cache_misses_total = Counter(
    "tts_cache_misses_total",
    "Total number of cache misses",
    ["cache_type"],
    registry=metrics_registry,
)

voice_uploaded_total = Counter(
    "tts_voices_uploaded_total",
    "Total voices uploaded",
    registry=metrics_registry,
)

characters_synthesized_total = Counter(
    "tts_characters_synthesized_total",
    "Total characters synthesized",
    registry=metrics_registry,
)

# ============ Histograms ============

synthesis_duration_seconds = Histogram(
    "tts_synthesis_duration_seconds",
    "Synthesis request duration in seconds",
    ["model"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    registry=metrics_registry,
)

inference_duration_seconds = Histogram(
    "tts_inference_duration_seconds",
    "Model inference duration in seconds",
    ["model"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0),
    registry=metrics_registry,
)

voice_encoding_duration_seconds = Histogram(
    "tts_voice_encoding_duration_seconds",
    "Voice encoding duration in seconds",
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0),
    registry=metrics_registry,
)

queue_processing_duration_seconds = Histogram(
    "tts_queue_processing_duration_seconds",
    "Time spent waiting in queue",
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0),
    registry=metrics_registry,
)

# ============ Gauges ============

active_synthesis_jobs = Gauge(
    "tts_active_synthesis_jobs",
    "Number of currently active synthesis jobs",
    registry=metrics_registry,
)

active_workers = Gauge(
    "tts_active_workers",
    "Number of active Celery workers",
    ["queue"],
    registry=metrics_registry,
)

gpu_utilization_percent = Gauge(
    "tts_gpu_utilization_percent",
    "GPU utilization percentage",
    ["gpu_id"],
    registry=metrics_registry,
)

gpu_memory_usage_percent = Gauge(
    "tts_gpu_memory_usage_percent",
    "GPU memory usage percentage",
    ["gpu_id"],
    registry=metrics_registry,
)

cache_size_bytes = Gauge(
    "tts_cache_size_bytes",
    "Redis cache size in bytes",
    registry=metrics_registry,
)

queue_depth = Gauge(
    "tts_queue_depth",
    "Number of pending jobs in queue",
    ["queue"],
    registry=metrics_registry,
)

database_connection_pool_size = Gauge(
    "tts_database_connection_pool_size",
    "Current database connection pool size",
    registry=metrics_registry,
)

failed_jobs = Gauge(
    "tts_failed_jobs",
    "Number of failed jobs",
    registry=metrics_registry,
)


# ============ Metrics Collectors ============

class MetricsCollector:
    """Helper class for recording metrics."""
    
    @staticmethod
    def record_synthesis_start(model: str = "tortoise"):
        """Record the start of a synthesis request."""
        active_synthesis_jobs.inc()
    
    @staticmethod
    def record_synthesis_complete(
        duration: float,
        success: bool = True,
        model: str = "tortoise",
        num_characters: int = 0,
    ):
        """Record the completion of a synthesis request."""
        active_synthesis_jobs.dec()
        synthesis_duration_seconds.labels(model=model).observe(duration)
        
        status = "success" if success else "failure"
        synthesis_requests_total.labels(status=status, model=model).inc()
        
        if num_characters > 0:
            characters_synthesized_total.inc(num_characters)
    
    @staticmethod
    def record_synthesis_failure(error_type: str = "unknown"):
        """Record a synthesis failure."""
        active_synthesis_jobs.dec()
        synthesis_failures_total.labels(error_type=error_type).inc()
        failed_jobs.inc()
    
    @staticmethod
    def record_cache_hit(cache_type: str = "audio"):
        """Record a cache hit."""
        cache_hits_total.labels(cache_type=cache_type).inc()
    
    @staticmethod
    def record_cache_miss(cache_type: str = "audio"):
        """Record a cache miss."""
        cache_misses_total.labels(cache_type=cache_type).inc()
    
    @staticmethod
    def record_voice_uploaded():
        """Record voice upload."""
        voice_uploaded_total.inc()
    
    @staticmethod
    def record_inference_time(duration: float, model: str = "tortoise"):
        """Record model inference time."""
        inference_duration_seconds.labels(model=model).observe(duration)
    
    @staticmethod
    def record_voice_encoding_time(duration: float):
        """Record voice encoding time."""
        voice_encoding_duration_seconds.observe(duration)
    
    @staticmethod
    def record_queue_wait_time(duration: float):
        """Record time spent waiting in queue."""
        queue_processing_duration_seconds.observe(duration)
    
    @staticmethod
    def set_active_workers(queue: str, count: int):
        """Set the number of active workers."""
        active_workers.labels(queue=queue).set(count)
    
    @staticmethod
    def set_gpu_utilization(gpu_id: int, utilization: float):
        """Set GPU utilization percentage."""
        gpu_utilization_percent.labels(gpu_id=str(gpu_id)).set(utilization)
    
    @staticmethod
    def set_gpu_memory_usage(gpu_id: int, usage: float):
        """Set GPU memory usage percentage."""
        gpu_memory_usage_percent.labels(gpu_id=str(gpu_id)).set(usage)
    
    @staticmethod
    def set_cache_size(size_bytes: int):
        """Set Redis cache size."""
        cache_size_bytes.set(size_bytes)
    
    @staticmethod
    def set_queue_depth(queue: str, depth: int):
        """Set queue depth."""
        queue_depth.labels(queue=queue).set(depth)
    
    @staticmethod
    def set_db_pool_size(size: int):
        """Set database connection pool size."""
        database_connection_pool_size.set(size)


logger = logging.getLogger(__name__)

# Example usage as context manager
class SynthesisTimer:
    """Context manager for timing synthesis operations."""
    
    def __init__(self, model: str = "tortoise"):
        self.model = model
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        MetricsCollector.record_synthesis_start(self.model)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        
        if exc_type:
            MetricsCollector.record_synthesis_failure(str(exc_type.__name__))
        else:
            MetricsCollector.record_synthesis_complete(duration, True, self.model)
