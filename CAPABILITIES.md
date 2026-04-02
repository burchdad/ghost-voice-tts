# Ghost Voice TTS Capabilities

This file provides a concise, operational view of what the service supports today.

## Synthesis Capabilities

- Multi-mode synthesis tiers:
  - realtime
  - balanced
  - high_quality
- Advanced prosody and SSML controls:
  - prosody templates
  - hierarchical prosody application
  - phrase-level modulation
- Emotion modulation:
  - primary and secondary emotion blending
  - intensity and curve controls
- Voice continuity:
  - session-level continuity via session identifiers
  - deterministic generation via voice seeds
- Streaming and continuity:
  - progressive sentence-level streaming
  - predictive prefetch
  - audio stitching and crossfade smoothing

## Voice Platform Capabilities

- Zero-shot and multi-sample voice cloning
- Voice embedding extraction and caching
- Voice marketplace lifecycle:
  - contribution workflows
  - moderation and verification
  - public/private listing controls

## Reliability and Routing Capabilities

- Priority queue routing by synthesis mode
- Provider routing with resilience controls:
  - circuit breakers
  - health states
  - fallback sequencing
  - adaptive weighted routing
- Distributed asynchronous processing with Celery
- Redis-backed caching and state continuity

## Observability Capabilities

- Health and readiness endpoints
- Prometheus metrics and structured logs
- Admin analytics and operational reporting
- Prosody learning observability endpoints (admin-only):
  - GET /admin/debug/prosody-learning
  - GET /admin/debug/prosody-learning/aggregate

### Prosody Learning Endpoint Posture

- Snapshot endpoint includes bounded rows with masked session IDs.
- Aggregate endpoint is stricter and returns only summary metrics.
- Neither endpoint is available without admin authorization.

## Security Capabilities

- JWT authentication and API key access
- Admin role protection for operational endpoints
- Request signing and verification
- Rate limiting per user/IP/endpoint
- Security auditing for admin actions

## Deployment Capabilities

- Docker-first development and deployment
- Kubernetes manifests for API, workers, and database
- Cloud, edge, and hybrid provider profile support

## SDK Capabilities

- Python SDK for synthesis, streaming, batch jobs, and voice management
- Job polling and typed response helpers

## Notes

- For endpoint-level examples and payload schemas, use README and API docs.
- For infrastructure and rollout details, use production readiness and implementation guides.
