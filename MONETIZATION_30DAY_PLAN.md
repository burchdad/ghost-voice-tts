# Monetization Quick Start: 30-Day Implementation Plan

**Goal:** Launch billing and enterprise infrastructure  
**Timeline:** 4 weeks (can compress to 2-3 weeks if full-time)  
**Target Revenue:** First $5K-10K MRR by end of sprint

---

## Week 1: Stripe Billing Foundation

### Day 1-2: Setup (4-6 hours)

**Morning:**
- [ ] Create Stripe account (https://dashboard.stripe.com)
- [ ] Verify business info and add payment method
- [ ] Generate API keys (sk_test_, pk_test_)
- [ ] Save keys to `.env` file

**Afternoon:**
- [ ] Review Stripe Billing docs (30 min)
- [ ] Plan pricing tiers:
  - **Free:** 100K chars/month, no charge
  - **Starter:** $50/month, 1M chars, $15 per M overage
  - **Pro:** $500/month, 10M chars, $12 per M overage
  - **Enterprise:** Custom pricing
- [ ] Add users to Stripe test environment

**Required Files:**
- `.env` with STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PUBLISHABLE_KEY

---

### Day 3-4: Database & Models (6-8 hours)

**Implement:**
- [ ] Add Subscription, Invoice, UsageEvent models to `app/models/db.py`
- [ ] Add Stripe fields to User model (stripe_customer_id, stripe_subscription_id, tier)
- [ ] Run database migration: `alembic revision --autogenerate -m "Add billing"`
- [ ] Run migration: `alembic upgrade head`

**Testing:**
- [ ] Verify tables created: `psql` → `\dt`
- [ ] Check schema with `alembic history`

**Files Modified:**
- `app/models/db.py` - 80 lines added
- `alembic/versions/*.py` - Auto-generated

---

### Day 5: Stripe Service (8-10 hours)

**Implement `app/services/billing.py`:**
- [ ] Create BillingManager class
- [ ] Implement `create_stripe_customer()`
- [ ] Implement `create_subscription()`
- [ ] Implement `cancel_subscription()`
- [ ] Implement `record_usage()`
- [ ] Implement `get_upcoming_invoice()`
- [ ] Implement webhook handlers

**Testing:**
- [ ] Unit tests for each method
- [ ] Mock Stripe API responses
- [ ] Test error handling

**Files Created:**
- `app/services/billing.py` - 250+ lines
- `tests/test_billing.py` - 150+ lines

---

## Week 2: API Endpoints & Webhooks

### Day 1-2: Billing Endpoints (6-8 hours)

**Implement in `app/main.py`:**
- [ ] `POST /billing/subscribe` - Subscribe to tier
- [ ] `POST /billing/cancel-subscription` - Cancel subscription
- [ ] `GET /billing/subscription` - Get current subscription
- [ ] `GET /billing/invoices` - List invoices
- [ ] `GET /billing/usage` - Get usage/cost
- [ ] `GET /billing/upcoming-invoice` - Preview next invoice

**Testing:**
- [ ] Postman collection with all endpoints
- [ ] Test with Stripe test cards
- [ ] Test error cases (duplicate subscription, invalid tier, etc.)

**Files Modified:**
- `app/main.py` - 150+ lines added

---

### Day 3-4: Webhooks Setup (6-8 hours)

**Webhook Endpoints:**
- [ ] `POST /webhooks/stripe` - Universal webhook handler
- [ ] Handle: `customer.subscription.updated`
- [ ] Handle: `customer.subscription.deleted`
- [ ] Handle: `invoice.payment_succeeded`
- [ ] Handle: `invoice.payment_failed`

**Testing Webhook:**
```bash
# Terminal 1: Start local server
python -m uvicorn app.main:app --reload

# Terminal 2: Forward Stripe webhooks to localhost
stripe listen --forward-to http://localhost:8000/webhooks/stripe

# Terminal 3: Trigger test events
stripe trigger customer.subscription.updated
```

**Integration with Synthesis:**
- [ ] Modify `/synthesize` endpoint to record usage
- [ ] Update user's `current_month_usage_characters`
- [ ] Call `billing_mgr.record_usage()`

**Files Modified:**
- `app/main.py` - +100 lines for webhook
- `app/services/billing.py` - +50 lines for webhook handlers

---

### Day 5: End-to-End Testing (4-6 hours)

**Test Workflow:**
1. Create test user
2. Call `POST /billing/subscribe?tier=starter`
3. Verify Stripe customer created
4. Verify subscription in database
5. Call `POST /synthesize` with text
6. Verify usage recorded
7. Check `/billing/usage` shows cost

**Fix Any Issues:**
- [ ] Test with real Stripe webhooks
- [ ] Test payment failures
- [ ] Test edge cases

---

## Week 3: Documentation Portal

### Day 1-2: Mintlify Setup (4-6 hours)

**Installation & Setup:**
```bash
# Install Mintlify CLI
npm install -g mintlify

# Create docs directory
mintlify init docs

# Create structure
mkdir -p docs/{api-reference,guides,integrations,images}
```

**Initial Files:**
- [ ] `docs/mint.json` - Site config, navigation
- [ ] `docs/index.mdx` - Homepage
- [ ] `docs/intro.mdx` - Getting started

**Configuration:**
```json
{
  "name": "Ghost Voice TTS",
  "logo": {
    "light": "/logo/light.svg",
    "dark": "/logo/dark.svg"
  },
  "favicon": "/favicon.png",
  "colors": {
    "primary": "#3b82f6",
    "light": "#eff6ff",
    "dark": "#0f172a"
  },
  "topbarLinks": [
    {
      "label": "API",
      "url": "https://api.ghostvoice.ai"
    }
  ],
  "anchors": [
    {
      "name": "GitHub",
      "icon": "github",
      "url": "https://github.com/burchdad/ghost-voice-tts"
    }
  ],
  "navigation": [
    {
      "group": "Getting Started",
      "pages": ["intro", "quickstart", "authentication"]
    },
    {
      "group": "API Reference",
      "pages": ["api/synthesis", "api/voices", "api/billing"]
    }
  ]
}
```

---

### Day 3-4: Content Creation (8-10 hours)

**Pages to Create:**

**1. Quickstart (`docs/quickstart.mdx`)**
```markdown
# Quickstart

Get your first synthesis in 5 minutes.

## 1. Get API Key
Visit https://api.ghostvoice.ai/keys

## 2. Install SDK
pip install ghost-voice-tts

## 3. Synthesize
from ghost_voice_tts import GhostVoiceTTS

client = GhostVoiceTTS(api_key="your-api-key")
response = client.synthesize(
    text="Hello, world!",
    voice_id="default"
)

response.save("audio.wav")
```

**2. Authentication (`docs/authentication.mdx`)**
- API keys
- JWT tokens
- Rate limiting

**3. Synthesis Guide (`docs/api/synthesis.mdx`)**
- Endpoint reference
- Parameters
- Response format
- Error codes

**4. Voices Guide (`docs/api/voices.mdx`)**
- Voice management
- Voice cloning
- Voice contributions

**5. Billing Guide (`docs/billing.mdx`)**
- Pricing tiers
- Usage tracking
- Invoice management
- Overage billing

**6. Streaming Guide (`docs/guides/streaming.mdx`)**
- WebSocket connection
- Audio chunks
- Error handling

**7. Python SDK (`docs/sdks/python.mdx`)**
- Installation
- Examples
- Best practices

---

### Day 5: Deploy & Launch (2-4 hours)

**Deploy to Mintlify Hosting:**
```bash
# Login to Mintlify
mintlify login

# Deploy
mintlify deploy

# Check status
mintlify status
```

**Setup Custom Domain:**
1. Go to Mintlify Dashboard
2. Connect domain: `docs.ghostvoice.ai`
3. Update DNS records
4. Verify SSL certificate

**Post-Launch:**
- [ ] Test all links work
- [ ] Verify authentication section
- [ ] Check code examples execute
- [ ] Test on mobile
- [ ] Get user feedback

---

## Week 4: Cloudflare Setup & Integration

### Day 1-2: Cloudflare Configuration (4-6 hours)

**DNS & Routing:**
```
1. Register domain: ghostvoice.ai
2. Add to Cloudflare
3. Update nameservers
4. Create subdomains:
   - api.ghostvoice.ai → FastAPI (8000)
   - docs.ghostvoice.ai → Mintlify
   - dashboard.ghostvoice.ai → Frontend
```

**Cloudflare Rules:**
- [ ] DDoS protection: Enabled (default)
- [ ] WAF: OWASP ModSecurity Core Rule Set
- [ ] Rate limiting: 100 requests/minute per IP
- [ ] Cache rules:
  - `/health` → bypass cache
  - `/metrics` → bypass cache
  - `/audio/*` → cache 30 days
  - `/synthesize` → bypass cache

**SSL/TLS:**
- [ ] Enable: Flexible (Cloudflare→origin)
- [ ] Enable: HTTPS redirects
- [ ] Minimum TLS: 1.2

---

### Day 3-4: API Gateway Configuration (4-6 hours)

**Kong Setup (Optional, If More Control Needed):**

If Cloudflare isn't enough:

```dockerfile
# docker-compose.yml - Add Kong service

kong:
  image: kong:3.4-alpine
  environment:
    KONG_DATABASE: postgres
    KONG_PG_HOST: postgres
    KONG_PG_USER: kong
    KONG_PG_PASSWORD: kong
    KONG_PROXY_ACCESS_LOG: /dev/stdout
    KONG_ADMIN_ACCESS_LOG: /dev/stdout
    KONG_PROXY_ERROR_LOG: /dev/stderr
    KONG_ADMIN_ERROR_LOG: /dev/stderr
    KONG_ADMIN_LISTEN: '0.0.0.0:8001'
  ports:
    - "8000:8000"  # Proxy
    - "8001:8001"  # Admin
  depends_on:
    - postgres
```

Or use AWS API Gateway with Lambda authorizers.

---

### Day 5: Test & Optimize (4-6 hours)

**Load Testing:**
```bash
# Test with Apache Bench
ab -n 1000 -c 10 http://api.ghostvoice.ai/health

# Results should show:
# - Latency < 50ms
# - Throughput > 100 req/sec
# - Error rate 0%
```

**Monitor:**
- [ ] Cloudflare Analytics: https://dash.cloudflare.com
- [ ] Check request volume
- [ ] Check cache hit rate (should be 60%+ for audio)
- [ ] Check DDoS metrics

---

## Launch Checklist

### Pre-Launch (End of Week 4)

- [ ] Stripe live keys added (sk_live_...)
- [ ] Webhook endpoints responding
- [ ] Test subscription created successfully
- [ ] Database backups automated
- [ ] Error logging configured
- [ ] Analytics tracking enabled
- [ ] Email notifications set up
- [ ] Cloudflare cache optimized
- [ ] Documentation deployed
- [ ] Status page created

### Launch Day

- [ ] Announce on social media
- [ ] Email to existing users
- [ ] Invite beta customers
- [ ] Monitor error rates
- [ ] Watch webhook delivery
- [ ] Check payment processing

### Post-Launch (Week 5)

- [ ] Gather user feedback
- [ ] Fix reported issues
- [ ] Monitor MRR growth
- [ ] Optimize pricing if needed
- [ ] Plan next features (CDN, Conversation AI)

---

## File Summary

### New Files to Create
```
app/services/billing.py (250+ lines)
tests/test_billing.py (150+ lines)
STRIPE_INTEGRATION_GUIDE.md
docs/mint.json
docs/index.mdx
docs/quickstart.mdx
docs/authentication.mdx
docs/api/synthesis.mdx
docs/api/voices.mdx
docs/api/billing.mdx
docs/guides/streaming.mdx
docs/sdks/python.mdx
```

### Files to Modify
```
app/models/db.py (+100 lines)
app/main.py (+250 lines)
app/core/config.py (+20 lines)
app/schemas/tts.py (+20 lines)
docker-compose.yml (add webhooks)
.env (add Stripe keys)
requirements.txt (add stripe, mintlify)
```

---

## Quick Win Metrics

### By end of Week 1
- [ ] Stripe configured
- [ ] Database ready
- [ ] BillingManager implemented

### By end of Week 2
- [ ] Endpoints working
- [ ] Webhooks receiving events
- [ ] Test subscriptions created
- [ ] Usage metering active

### By end of Week 3
- [ ] Docs portal live
- [ ] Content published
- [ ] Custom domain working

### By end of Week 4
- [ ] Cloudflare configured
- [ ] Rate limiting active
- [ ] CDN caching audio
- [ ] Ready for customers

---

## Revenue Ramp

```
Week 1-2: Setup (no revenue)
Week 3-4: Early adopters ($2-3K)
Month 2: Word of mouth ($5-10K)
Month 3: Sales outreach ($15-25K)
Month 4+: Viral growth? ($50K+)
```

---

## Next Steps After Launch

1. **Month 2: Advanced Billing**
   - Tax calculation (TaxJar)
   - Dunning management (retry failed payments)
   - Usage alerts (warn before overage)
   - Custom invoicing

2. **Month 3: Conversation AI**
   - WebRTC streaming
   - STT (Whisper)
   - LLM integration (GPT-4)
   - Full voice agent platform

3. **Month 4: Enterprise Features**
   - Voice security/watermarking
   - SLA enforcement
   - Dedicated support
   - Custom contracts

---

## Success Criteria

✅ **Launch Success:**
- First 10 paying customers
- $500+ MRR
- Zero critical bugs in billing
- 99% webhook delivery

✅ **Month 1 Goals:**
- 50 paying customers
- $2,500+ MRR
- < 1% payment failure rate
- < 100ms API latency (p99)

✅ **Month 3 Goals:**
- 200 paying customers
- $15,000+ MRR
- Enterprise contract signed
- Conversation AI beta

---

## Support & Resources

- **Stripe Support:** https://support.stripe.com
- **Mintlify Community:** https://mintlify.com/docs
- **AWS API Gateway:** https://docs.aws.amazon.com/apigateway/
- **Kong Docs:** https://docs.konghq.com/

---

**Now go build! 🚀**
