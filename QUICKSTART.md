# Quick Start Guide

## 30-Second Setup

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Start all services with Docker Compose
docker-compose up -d

# 3. Wait for services to be healthy
sleep 10

# 4. Test the API
curl http://localhost:8000/health
```

You should see:
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "database": "connected",
  "redis": "connected",
  "tts_model": "tortoise",
  "timestamp": "2024-03-04T10:00:00Z"
}
```

## Create a Voice

```bash
curl -X POST http://localhost:8000/voices/create \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-token" \
  -d '{
    "name": "Alex",
    "description": "Natural male voice",
    "gender": "male",
    "accent": "American",
    "language": "en"
  }'
```

Response:
```json
{
  "id": "voice-xxx",
  "name": "Alex",
  "language": "en",
  ...
}
```

## Synthesize Speech

```bash
curl -X POST http://localhost:8000/synthesize \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test-token" \
  -d '{
    "text": "Hello, this is Ghost Voice TTS!",
    "voice_id": "voice-xxx",
    "language": "en",
    "style": "normal",
    "speed": 1.0,
    "pitch": 1.0
  }'
```

Response:
```json
{
  "id": "job-yyy",
  "status": "pending",
  "progress": 0.0,
  "created_at": "2024-03-04T10:00:00Z"
}
```

## Check Synthesis Status

```bash
curl http://localhost:8000/synthesis/job-yyy
```

Response:
```json
{
  "id": "job-yyy",
  "status": "completed",
  "audio_url": "s3://ghost-voice-tts/audio/job-yyy.wav",
  "audio_duration": 3.5,
  "progress": 1.0,
  "inference_time_ms": 2450,
  "completed_at": "2024-03-04T10:00:03Z"
}
```

## Monitor Tasks (Flower)

Open: http://localhost:5555

See all synthesis and voice cloning tasks in real-time.

## Check API Documentation

Open: http://localhost:8000/docs

Interactive Swagger UI with all endpoints documented.

## Stopping Services

```bash
docker-compose down
```

## Next Steps

1. **Upload voice samples:** POST `/voices/{voice_id}/upload-sample` with audio file
2. **Batch synthesis:** POST `/synthesize-batch` to process multiple texts
3. **Stream synthesis:** GET `/synthesis/{job_id}/stream` for WebSocket streaming
4. **Deploy to K8s:** Follow instructions in README.md for production deployment

## Railway Production Deploy

1. Push this repository to GitHub.
2. In Railway, create a new project from this GitHub repository.
3. Railway will detect `railway.json` and build with the root `Dockerfile`.
4. Add the following required environment variables in Railway:

```bash
DEBUG=False
HOST=0.0.0.0
TTS_DEVICE=cpu
DATABASE_URL=<Railway Postgres URL>
REDIS_URL=<Railway Redis URL>
CELERY_BROKER_URL=<Railway Redis URL>
CELERY_RESULT_BACKEND_URL=<Railway Redis URL>
SECRET_KEY=<long-random-secret>
ENFORCE_SECURE_DEFAULTS=True
TTS_REQUIRE_REAL_MODEL=False
TTS_ALLOW_SYNTH_FALLBACK=True
SYNTHESIS_QUEUE_POLICY=inline-fallback
PUBLIC_BASE_URL=https://<your-railway-domain>
CORS_ALLOWED_ORIGINS=["https://<your-landing-page-domain>"]
```

5. After deploy, verify:

```bash
curl https://<your-railway-domain>/health
```

6. Point your landing page API base URL to `https://<your-railway-domain>`.

## Troubleshooting

### API not responding
```bash
docker-compose logs api
```

### Database connection error
```bash
docker-compose logs postgres
```

### Redis connection error
```bash
docker-compose logs redis
```

### Celery workers not processing
```bash
docker-compose logs celery-worker-synthesis
docker-compose logs flower  # See Flower dashboard for worker status
```

## Performance Tips

- Set `TTS_BATCH_SIZE=8` for higher throughput (if you have GPU memory)
- Enable caching with `TTS_ENABLE_CACHING=True`
- Use `speed=0.8-1.2` for natural-sounding output
- Upload at least 3 voice samples for best voice cloning quality

## Architecture

```
┌─ API Server (port 8000)
│  └─ REST endpoints for synthesis & voice management
├─ Synthesis Workers (GPU x2)
│  └─ Tortoise TTS inference
├─ Voice Cloning Workers (GPU x1)
│  └─ Speaker encoding
├─ PostgreSQL (port 5432)
├─ Redis (port 6379)
└─ Flower Monitoring (port 5555)
```

---

For full documentation, see [README.md](README.md)
