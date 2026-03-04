# Admin Dashboard & Analytics Dashboard - Implementation Details

## 📊 Admin Dashboard (`app/services/admin_dashboard.py` & `app/routes/admin.py`)

### Purpose
Administrative operations interface for system operators to manage users, moderate content, and monitor system health.

### Service Layer - `AdminDashboardManager`

#### User Management
```python
# Get users with filtering
get_users(skip: int, limit: int, tier: str, search: str) -> List[User]
# Get detailed user metrics
get_user_metrics(user_id: str) -> dict
# Adjust monthly quota (e.g., grant extra for support issues)
adjust_user_quota(user_id: str, adjustment: int, reason: str) -> bool
# Upgrade user tier (free → starter → pro → enterprise)
upgrade_user_tier(user_id: str, new_tier: str) -> bool
# Suspend user account
suspend_user(user_id: str, reason: str) -> bool
```

#### Voice Moderation
```python
# Get voices pending verification (public flag + not verified)
get_pending_voices(skip: int, limit: int) -> List[Voice]
# Mark voice as verified and approved for quality
verify_voice(voice_id: str) -> bool
# Reject voice from public listing (abuse, low quality, etc.)
reject_voice(voice_id: str, reason: str) -> bool
```

#### System Monitoring
```python
# Overall system health metrics
get_system_health() -> dict
  # Returns: users count, synthesis activity, success rate, performance stats

# Top users by metric (synthesis count, characters processed)
get_top_users(metric: str, limit: int) -> List[dict]
```

#### Marketplace Insights
```python
# Marketplace performance metrics
get_marketplace_insights() -> dict
  # Returns: total contributions, active trials, conversion rate
```

#### Reporting
```python
# Daily usage report
generate_daily_report() -> dict
  # Returns: jobs, completed, failed, characters, latency
```

### API Endpoints - `app/routes/admin.py`

**Auth:** Admin role required (verified via `verify_admin` dependency)

#### User Management
- `GET /admin/users?skip={skip}&limit={limit}&tier={tier}&search={search}`
  - List users with filtering
- `GET /admin/users/{user_id}/metrics`
  - Get detailed user metrics (synthesis jobs, quota usage, voices)
- `POST /admin/users/{user_id}/quota?adjustment={int}&reason={str}`
  - Adjust monthly quota
- `POST /admin/users/{user_id}/tier?new_tier={tier}`
  - Upgrade user tier
- `POST /admin/users/{user_id}/suspend?reason={str}`
  - Suspend user account

#### Voice Moderation
- `GET /admin/voices/pending?skip={skip}&limit={limit}`
  - Get voices pending verification
- `POST /admin/voices/{voice_id}/verify`
  - Verify voice as approved
- `POST /admin/voices/{voice_id}/reject?reason={str}`
  - Reject voice from public listing

#### System Monitoring
- `GET /admin/health`
  - System health metrics (users, synthesis activity, performance)
- `GET /admin/top-users?metric={metric}&limit={limit}`
  - Top users by synthesis count or characters

#### Marketplace
- `GET /admin/marketplace/insights`
  - Marketplace performance (contributions, trials, conversions)

#### Reports
- `GET /admin/reports/daily`
  - Daily usage report (yesterday's stats)

---

## 📈 Analytics Dashboard (`app/services/analytics.py` & `app/routes/analytics.py`)

### Purpose
Customer-facing analytics for tracking synthesis usage, performance, quota, and marketplace engagement.

### Service Layer - `AnalyticsDashboard`

#### Usage Analytics
```python
# User's synthesis usage stats for period
get_user_usage_stats(user_id: str, days: int) -> dict
  # Returns: total jobs, characters, average job size, success rate, daily average

# Day-by-day breakdown
get_daily_usage(user_id: str, days: int) -> List[dict]
  # Returns: [{date, jobs, characters, success, failed}, ...]

# Usage per voice
get_voice_usage_stats(user_id: str) -> List[dict]
  # Returns: [{voice_id, total_jobs, completed, failed, characters, avg_latency_ms}, ...]
```

#### Performance Analytics
```python
# Synthesis latency metrics
get_synthesis_performance(user_id: Optional[str]) -> dict
  # Returns: p50, p95, p99 latencies + avg, min, max

# Error rate analysis
get_error_analytics(user_id: Optional[str], days: int) -> dict
  # Returns: error_rate, failed jobs, errors by type breakdown
```

#### Quota Analytics
```python
# Monthly quota usage and projections
get_quota_analytics(user_id: str) -> dict
  # Returns: monthly quota, current usage, remaining, usage %, projected usage
```

#### Voice Quality
```python
# Quality score distribution across all voices
get_voice_quality_scores() -> List[dict]
  # Returns: excellent (>=0.9), good (0.7-0.9), fair (0.5-0.7), poor (<0.5)
```

#### Marketplace Analytics
```python
# Marketplace revenue (estimated from trial grants)
get_marketplace_revenue_analytics(days: int) -> dict
  # Returns: trials granted, conversions, conversion rate, estimated revenue

# Marketplace statistics
get_voice_marketplace_stats() -> dict
  # Returns: public voices count, contributions, top languages
```

#### Trends
```python
# Growth indicators over period
get_usage_trends(days: int) -> dict
  # Returns: job growth %, character growth %, trend direction (up/down/flat)
```

### API Endpoints - `app/routes/analytics.py`

**Auth:** User must be authenticated (verified via `get_current_user` dependency)

#### Usage Stats
- `GET /analytics/usage/summary?days={days}`
  - User's synthesis statistics (30 days default)
- `GET /analytics/usage/daily?days={days}`
  - Day-by-day breakdown
- `GET /analytics/usage/by-voice`
  - Usage per voice

#### Performance
- `GET /analytics/performance`
  - Latency metrics (p50, p95, p99)
- `GET /analytics/errors?days={days}`
  - Error rate and error types

#### Quota
- `GET /analytics/quota`
  - Monthly quota usage and projection

#### Quality
- `GET /analytics/quality/distribution`
  - Voice quality score distribution

#### Marketplace
- `GET /analytics/marketplace/revenue?days={days}`
  - Marketplace revenue analytics
- `GET /analytics/marketplace/voices`
  - Marketplace statistics

#### Trends
- `GET /analytics/trends?days={days}`
  - Growth trends and direction

#### Dashboard
- `GET /analytics/dashboard`
  - Complete dashboard summary (all stats in one call)

---

## 🔌 Integration with Main App

### Router Imports
Added to `app/main.py`:
```python
from app.routes import admin, analytics

# Include routers
app.include_router(admin.router)
app.include_router(analytics.router)
```

### Dependency Management
Updated `app/dependencies.py`:
- Added `verify_admin` alias for admin role checking
- Extends existing `get_current_user` dependency

---

## 📊 Analytics Features Breakdown

### For Users
- **Usage Tracking:** See how much TTS you've used (jobs, characters)
- **Performance Insights:** Check average latencies at different percentiles
- **Error Diagnosis:** See which errors occurred and fix them
- **Quota Management:** Monitor monthly quota usage and projections
- **Voice Analytics:** See which voices are most used and performant
- **Marketplace Status:** Track trial period usage and membership status
- **Growth Trends:** Visualize usage trends over time

### For Admins
- **User Management:** Search, filter, and manage users across all tiers
- **Quota Adjustments:** Grant emergency quota to support customers
- **Tier Upgrades:** Manually upgrade users based on Business needs
- **Voice Moderation:** Verify or reject public voice contributions
- **System Health:** Monitor overall service health and performance
- **Top Users:** Identify power users and potential upsell opportunities
- **Daily Reports:** Automated daily usage summaries

---

## 🚀 Usage Examples

### Admin API
```bash
# Get users on pro tier
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.ghost-voice.com/admin/users?tier=pro"

# Get user's synthesis metrics
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.ghost-voice.com/admin/users/user-123/metrics"

# Adjust user quota (service credit)
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.ghost-voice.com/admin/users/user-123/quota?adjustment=1000000&reason=Support%20credit"

# Upgrade user to pro
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.ghost-voice.com/admin/users/user-123/tier?new_tier=pro"

# Get pending voice verifications
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.ghost-voice.com/admin/voices/pending?limit=50"

# Verify voice
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.ghost-voice.com/admin/voices/voice-456/verify"

# Get system health
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://api.ghost-voice.com/admin/health"
```

### Analytics API
```bash
# Get usage summary (30 days)
curl -H "Authorization: Bearer $USER_TOKEN" \
  "https://api.ghost-voice.com/analytics/usage/summary"

# Get daily breakdown
curl -H "Authorization: Bearer $USER_TOKEN" \
  "https://api.ghost-voice.com/analytics/usage/daily?days=30"

# Check performance (latency percentiles)
curl -H "Authorization: Bearer $USER_TOKEN" \
  "https://api.ghost-voice.com/analytics/performance"

# Check quota usage
curl -H "Authorization: Bearer $USER_TOKEN" \
  "https://api.ghost-voice.com/analytics/quota"

# Get complete dashboard
curl -H "Authorization: Bearer $USER_TOKEN" \
  "https://api.ghost-voice.com/analytics/dashboard"

# Check marketplace status
curl -H "Authorization: Bearer $USER_TOKEN" \
  "https://api.ghost-voice.com/analytics/marketplace/revenue?days=30"
```

---

## 🛡️ Security Considerations

### Admin Endpoints
- Protected by `verify_admin` dependency
- Only users with `is_admin=True` can access
- All admin actions are logged for audit trail
- Rate limiting applied per admin account

### Analytics Endpoints
- Protected by `get_current_user` dependency
- Users only see their own analytics
- Public marketplace stats available to all authenticated users
- Rate limiting prevents abuse of analytics API

### Data Privacy
- Admin quota adjustments logged with reason
- User suspensions require admin authentication
- Voice moderation tracked
- No sensitive data exposed in analytics

---

## 📈 Metrics Collected

### Usage Metrics
- Total synthesis jobs per user
- Total characters synthesized
- Success rate percentage
- Average job size
- Daily usage patterns

### Performance Metrics
- Average latency (ms)
- 50th percentile latency (median)
- 95th percentile latency
- 99th percentile latency (tail)
- Min/max latencies

### Error Metrics
- Error rate (%)
- Errors by type
- Failed jobs count
- Success percentage

### Quota Metrics
- Monthly quota limit
- Current usage
- Remaining quota
- Usage percentage
- Projected usage for month

### Marketplace Metrics
- Active voice contributions
- Free trials granted
- User conversions from trial to paid
- Estimated revenue impact
- Top languages available

---

## 🔄 Data Flow

```
User → Analytics Endpoints → AnalyticsDashboard
                                    ↓
                            Query SynthesisJob
                            Query Voice
                            Query User
                                    ↓
                            Calculate Metrics
                            Build Response
                                    ↓
                            Return to User

Admin → Admin Endpoints → AdminDashboardManager
                                ↓
                        Query User/Voice
                        Modify Data
                        Log Action
                                ↓
                        Return Result
```

---

## 🎯 Next Steps for Deployment

1. **Test Endpoints:** Run integration tests against new endpoints
2. **Verify Permissions:** Confirm admin role checking works
3. **Load Test:** Test analytics queries with large datasets
4. **Monitoring:** Set up alerts for admin dashboard usage
5. **Documentation:** Add to API reference and user guides
6. **Rollout:** Deploy to production with feature flags
