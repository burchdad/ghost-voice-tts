# Stripe Billing Integration Guide

**Status: Implementation Blueprint**
**Priority: Critical for Monetization**
**Estimated Time: 3-4 weeks**

---

## Overview

This guide provides step-by-step implementation of Stripe billing for ghost-voice-tts. By the end, you'll have:

- ✅ Monthly subscriptions with recurring billing
- ✅ Usage-based metering (per-character pricing)
- ✅ Automatic invoicing
- ✅ Stripe webhook handling
- ✅ Billing dashboard endpoints
- ✅ Overage alerting

---

## Architecture

```
User (Frontend)
     ↓
    API
     ├─ POST /billing/subscribe
     ├─ POST /billing/updatePaymentMethod
     ├─ GET  /billing/subscription
     ├─ GET  /billing/invoices
     └─ GET  /billing/usage
     
     ↓
Stripe SDK
     ├─ Create customer
     ├─ Create subscription
     ├─ Record usage (meter)
     ├─ Generate invoices
     └─ Send webhooks

     ↓
Database
     ├─ Subscription model
     ├─ Invoice model
     ├─ UsageEvent model
     └─ PaymentMethod model
```

---

## Step 1: Database Schema

### Add Models to `app/models/db.py`

```python
# Add these fields to User model:
class User(SQLModel, table=True):
    # ... existing fields ...
    
    # Stripe integration
    stripe_customer_id: Optional[str] = Field(default=None, unique=True, index=True)
    stripe_subscription_id: Optional[str] = Field(default=None, unique=True)
    
    # Subscription metadata
    tier: str = Field(default="free")  # free, starter, pro, enterprise
    subscription_status: str = Field(default="inactive")  # active, paused, canceled
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    billing_email: Optional[str] = None
    
    # Usage tracking
    current_month_usage_characters: int = Field(default=0)
    current_month_cost: float = Field(default=0.0)  # in cents
    total_spent: float = Field(default=0.0)

class Subscription(SQLModel, table=True):
    """User subscription details."""
    __tablename__ = "subscriptions"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    
    # Stripe IDs
    stripe_subscription_id: str = Field(unique=True, index=True)
    stripe_customer_id: str
    stripe_price_id: str  # Product->Price mapping
    
    # Subscription details
    tier: str  # starter, pro, enterprise
    status: str  # active, past_due, paused, canceled
    
    # Billing cycle
    current_period_start: datetime
    current_period_end: datetime
    renewal_attempts: int = Field(default=0)
    
    # Metering data
    monthly_character_limit: int  # How many characters included
    monthly_price_cents: int  # Base subscription price
    
    # Features
    auto_renew: bool = Field(default=True)
    
    # Relationships
    user: User = Relationship(back_populates="subscriptions")
    invoices: List["Invoice"] = Relationship(back_populates="subscription")
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    canceled_at: Optional[datetime] = None


class Invoice(SQLModel, table=True):
    """Monthly invoices for subscriptions."""
    __tablename__ = "invoices"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    subscription_id: str = Field(foreign_key="subscriptions.id")
    
    # Stripe reference
    stripe_invoice_id: str = Field(unique=True, index=True)
    stripe_status: str  # draft, open, paid, uncollectible, void
    
    # Amount breakdown (in cents)
    base_amount: int  # Subscription base price
    usage_amount: int  # Overage charges
    tax_amount: int
    total_amount: int
    
    # Billing period
    period_start: datetime
    period_end: datetime
    
    # Payment status
    paid: bool = Field(default=False)
    paid_at: Optional[datetime] = None
    due_date: Optional[datetime] = None
    
    # PDF & receipts
    invoice_pdf_url: Optional[str] = None
    
    # Relationships
    user: User = Relationship(back_populates="invoices")
    subscription: Subscription = Relationship(back_populates="invoices")
    usage_events: List["UsageEvent"] = Relationship(back_populates="invoice")
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UsageEvent(SQLModel, table=True):
    """Tracks billable usage for metered billing."""
    __tablename__ = "usage_events"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    invoice_id: Optional[str] = Field(default=None, foreign_key="invoices.id")
    synthesis_job_id: Optional[str] = None
    
    # Stripe metering
    stripe_subscription_item_id: str  # The meter to update
    
    # Usage details
    event_type: str  # synthesis_character, voice_clone, api_call
    quantity: int  # How many chars/clones/calls
    unit_price_cents: int  # Price per unit
    total_cost_cents: int  # quantity * unit_price_cents
    
    # Metadata
    description: str  # Human readable: "1000 characters synthesized"
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    user: User = Relationship(back_populates="usage_events")
    invoice: Optional[Invoice] = Relationship(back_populates="usage_events")


# Add to User model:
subscriptions: List["Subscription"] = Relationship(back_populates="user")
invoices: List["Invoice"] = Relationship(back_populates="user")
usage_events: List["UsageEvent"] = Relationship(back_populates="user")
```

---

## Step 2: Stripe Configuration

### `app/core/config.py` - Add Settings

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # ... existing settings ...
    
    # Stripe
    STRIPE_API_KEY: str = Field(...)  # sk_live_... in prod, sk_test_... in dev
    STRIPE_WEBHOOK_SECRET: str = Field(...)  # whsec_... from Stripe Dashboard
    STRIPE_PUBLISHABLE_KEY: str = Field(...)  # pk_live_... for frontend
    
    # Pricing (in cents)
    STRIPE_PRICING: dict = {
        "starter": {
            "price_id": "price_...",  # From Stripe Dashboard
            "monthly_cents": 5000,  # $50/month
            "character_limit": 1_000_000,
            "overage_price_per_million_cents": 1500,  # $15 per M chars
        },
        "pro": {
            "price_id": "price_...",
            "monthly_cents": 50000,  # $500/month
            "character_limit": 10_000_000,
            "overage_price_per_million_cents": 1200,
        },
        "enterprise": {
            "price_id": None,  # Custom pricing
            "monthly_cents": 0,
            "character_limit": None,
            "overage_price_per_million_cents": 0,
        },
    }
```

### Stripe Dashboard Setup

```
1. Go to https://dashboard.stripe.com

2. Create Products:
   - Name: "Ghost Voice Starter"
   - Type: Service
   - Pricing: $50/month (recurring)
   
   - Name: "Ghost Voice Pro"
   - Type: Service
   - Pricing: $500/month (recurring)
   
   - Name: "Ghost Voice Enterprise"
   - Type: Service
   - Pricing: Custom

3. Create Metered Prices (for overage billing):
   - Meter: "Characters Synthesized"
   - Billing scheme: "Per unit"
   - Price: $15 per 1M characters
   
4. Create Webhooks:
   - customer.subscription.updated
   - customer.subscription.deleted
   - invoice.payment_succeeded
   - invoice.payment_failed
   - customer.updated
```

---

## Step 3: Billing Service

### `app/services/billing.py` - NEW FILE

```python
import stripe
import logging
from datetime import datetime, timedelta
from typing import Optional
from sqlmodel import Session

from app.core.config import get_settings
from app.models.db import User, Subscription, Invoice, UsageEvent

logger = logging.getLogger(__name__)
settings = get_settings()

# Setup Stripe
stripe.api_key = settings.STRIPE_API_KEY


class StripeError(Exception):
    """Raised when Stripe operations fail."""
    pass


class BillingManager:
    """Manage Stripe billing operations."""
    
    def __init__(self):
        self.stripe = stripe
        self.pricing = settings.STRIPE_PRICING
    
    # ============ Customer Management ============
    
    def create_stripe_customer(
        self,
        user: User,
        session: Session,
    ) -> str:
        """Create a customer in Stripe."""
        
        if user.stripe_customer_id:
            logger.warning(f"User {user.id} already has Stripe customer {user.stripe_customer_id}")
            return user.stripe_customer_id
        
        try:
            customer = stripe.Customer.create(
                email=user.email,
                name=user.username,
                metadata={"user_id": user.id}
            )
            
            user.stripe_customer_id = customer.id
            session.add(user)
            session.commit()
            
            logger.info(f"Created Stripe customer {customer.id} for user {user.id}")
            return customer.id
        
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create Stripe customer: {str(e)}")
            raise StripeError(f"Failed to create customer: {str(e)}")
    
    # ============ Subscription Management ============
    
    def create_subscription(
        self,
        user: User,
        tier: str,
        session: Session,
    ) -> dict:
        """Create a subscription for user."""
        
        if tier not in self.pricing:
            raise ValueError(f"Invalid tier: {tier}")
        
        # Ensure customer exists
        if not user.stripe_customer_id:
            self.create_stripe_customer(user, session)
        
        pricing = self.pricing[tier]
        
        try:
            subscription = stripe.Subscription.create(
                customer=user.stripe_customer_id,
                items=[
                    {
                        "price": pricing["price_id"],
                        "metadata": {"tier": tier}
                    }
                ],
                payment_behavior="default_incomplete",
                expand=["latest_invoice.payment_intent"],
                metadata={"user_id": user.id, "tier": tier}
            )
            
            # Save to database
            db_subscription = Subscription(
                user_id=user.id,
                stripe_subscription_id=subscription.id,
                stripe_customer_id=user.stripe_customer_id,
                stripe_price_id=pricing["price_id"],
                tier=tier,
                status=subscription.status,
                current_period_start=datetime.fromtimestamp(
                    subscription.current_period_start
                ),
                current_period_end=datetime.fromtimestamp(
                    subscription.current_period_end
                ),
                monthly_character_limit=pricing["character_limit"],
                monthly_price_cents=pricing["monthly_cents"],
            )
            session.add(db_subscription)
            
            # Update user
            user.stripe_subscription_id = subscription.id
            user.tier = tier
            user.subscription_status = subscription.status
            session.add(user)
            session.commit()
            
            logger.info(f"Created subscription {subscription.id} for user {user.id}")
            
            return {
                "subscription_id": subscription.id,
                "status": subscription.status,
                "client_secret": (
                    subscription.latest_invoice.payment_intent.client_secret
                    if subscription.status == "incomplete"
                    else None
                ),
            }
        
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create subscription: {str(e)}")
            raise StripeError(f"Failed to create subscription: {str(e)}")
    
    def cancel_subscription(
        self,
        user: User,
        session: Session,
        at_period_end: bool = True,
    ) -> dict:
        """Cancel user's subscription."""
        
        if not user.stripe_subscription_id:
            raise ValueError("User has no active subscription")
        
        try:
            subscription = stripe.Subscription.delete(
                user.stripe_subscription_id,
                invoice_now=not at_period_end,
            )
            
            # Update database
            db_sub = session.query(Subscription).filter(
                Subscription.stripe_subscription_id == user.stripe_subscription_id
            ).first()
            
            if db_sub:
                db_sub.status = "canceled"
                db_sub.canceled_at = datetime.utcnow()
                session.add(db_sub)
            
            user.subscription_status = "canceled"
            user.stripe_subscription_id = None
            user.tier = "free"
            session.add(user)
            session.commit()
            
            logger.info(f"Canceled subscription for user {user.id}")
            
            return {
                "status": "canceled",
                "subscription_id": subscription.id,
                "at_period_end": at_period_end,
            }
        
        except stripe.error.StripeError as e:
            logger.error(f"Failed to cancel subscription: {str(e)}")
            raise StripeError(f"Failed to cancel subscription: {str(e)}")
    
    # ============ Usage Metering ============
    
    def record_usage(
        self,
        user: User,
        quantity: int,
        session: Session,
        description: str = "Characters synthesized",
    ) -> None:
        """Record usage event for metering."""
        
        if not user.stripe_subscription_id:
            logger.warning(f"No subscription for user {user.id}, skipping metering")
            return
        
        # Update database
        usage_event = UsageEvent(
            user_id=user.id,
            stripe_subscription_item_id="",  # TODO: Get from subscription
            event_type="synthesis_character",
            quantity=quantity,
            unit_price_cents=0,  # Handled by Stripe
            total_cost_cents=0,
            description=description,
        )
        session.add(usage_event)
        session.commit()
        
        logger.info(f"Recorded {quantity} char usage for user {user.id}")
    
    # ============ Invoice Management ============
    
    def get_upcoming_invoice(
        self,
        user: User,
    ) -> Optional[dict]:
        """Get next invoice preview."""
        
        if not user.stripe_subscription_id:
            return None
        
        try:
            upcoming = stripe.Invoice.upcoming(
                customer=user.stripe_customer_id,
                subscription=user.stripe_subscription_id,
            )
            
            return {
                "amount_due": upcoming.amount_due / 100,
                "currency": upcoming.currency,
                "period_start": datetime.fromtimestamp(upcoming.period_start),
                "period_end": datetime.fromtimestamp(upcoming.period_end),
                "due_date": datetime.fromtimestamp(upcoming.due_date) if upcoming.due_date else None,
                "lines": [
                    {
                        "description": line.description,
                        "amount": line.amount / 100,
                        "quantity": line.quantity,
                    }
                    for line in upcoming.lines
                ]
            }
        
        except stripe.error.InvalidRequestError:
            logger.warning(f"No upcoming invoice for user {user.id}")
            return None
    
    # ============ Webhook Handling ============
    
    @staticmethod
    def verify_webhook_signature(body: str, signature: str) -> dict:
        """Verify Stripe webhook signature."""
        
        try:
            event = stripe.Webhook.construct_event(
                body,
                signature,
                settings.STRIPE_WEBHOOK_SECRET,
            )
            return event
        except ValueError:
            logger.error("Invalid webhook payload")
            raise
        except stripe.error.SignatureVerificationError:
            logger.error("Invalid webhook signature")
            raise
    
    @staticmethod
    def handle_subscription_updated(
        stripe_subscription_id: str,
        session: Session,
    ) -> None:
        """Handle subscription.updated webhook."""
        
        subscription = stripe.Subscription.retrieve(stripe_subscription_id)
        
        # Update database
        db_sub = session.query(Subscription).filter(
            Subscription.stripe_subscription_id == stripe_subscription_id
        ).first()
        
        if db_sub:
            db_sub.status = subscription.status
            db_sub.current_period_start = datetime.fromtimestamp(
                subscription.current_period_start
            )
            db_sub.current_period_end = datetime.fromtimestamp(
                subscription.current_period_end
            )
            session.add(db_sub)
            session.commit()
            
            logger.info(f"Updated subscription {stripe_subscription_id} status to {subscription.status}")
    
    @staticmethod
    def handle_invoice_payment_succeeded(
        stripe_invoice_id: str,
        session: Session,
    ) -> None:
        """Handle invoice.payment_succeeded webhook."""
        
        invoice = stripe.Invoice.retrieve(stripe_invoice_id)
        
        # Update database
        db_invoice = session.query(Invoice).filter(
            Invoice.stripe_invoice_id == stripe_invoice_id
        ).first()
        
        if db_invoice:
            db_invoice.paid = True
            db_invoice.paid_at = datetime.utcnow()
            db_invoice.stripe_status = invoice.status
            session.add(db_invoice)
            session.commit()
            
            logger.info(f"Invoice {stripe_invoice_id} marked as paid")


def get_billing_manager() -> BillingManager:
    """Dependency injection for BillingManager."""
    return BillingManager()
```

---

## Step 4: API Endpoints

### `app/main.py` - Add Billing Routes

```python
from fastapi import APIRouter, Header, Depends, HTTPException, Request
from app.services.billing import get_billing_manager, StripeError
from app.dependencies import get_current_user

billing_router = APIRouter(prefix="/billing", tags=["billing"])


@billing_router.post("/subscribe")
async def subscribe_to_tier(
    tier: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Create a subscription to a tier."""
    
    if tier not in ["starter", "pro", "enterprise"]:
        raise HTTPException(status_code=400, detail="Invalid tier")
    
    if user.tier == tier:
        raise HTTPException(status_code=400, detail="Already on this tier")
    
    billing_mgr = get_billing_manager()
    
    try:
        result = billing_mgr.create_subscription(user, tier, session)
        return result
    except StripeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@billing_router.post("/cancel-subscription")
async def cancel_subscription(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Cancel user's subscription."""
    
    if not user.stripe_subscription_id:
        raise HTTPException(status_code=400, detail="No active subscription")
    
    billing_mgr = get_billing_manager()
    
    try:
        result = billing_mgr.cancel_subscription(user, session)
        return result
    except StripeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@billing_router.get("/subscription")
async def get_subscription_info(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Get current subscription info."""
    
    subscription = session.query(Subscription).filter(
        Subscription.user_id == user.id
    ).first()
    
    if not subscription:
        return {"status": "no_subscription", "tier": "free"}
    
    return {
        "tier": subscription.tier,
        "status": subscription.status,
        "current_period_start": subscription.current_period_start,
        "current_period_end": subscription.current_period_end,
        "monthly_character_limit": subscription.monthly_character_limit,
        "monthly_price": subscription.monthly_price_cents / 100,
    }


@billing_router.get("/upcoming-invoice")
async def get_upcoming_invoice(
    user: User = Depends(get_current_user),
):
    """Get next invoice preview."""
    
    billing_mgr = get_billing_manager()
    invoice = billing_mgr.get_upcoming_invoice(user)
    
    if not invoice:
        raise HTTPException(status_code=400, detail="No upcoming invoice")
    
    return invoice


@billing_router.get("/invoices")
async def list_invoices(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    limit: int = 10,
    skip: int = 0,
):
    """List user's invoices."""
    
    invoices = session.query(Invoice).filter(
        Invoice.user_id == user.id
    ).order_by(Invoice.created_at.desc()).offset(skip).limit(limit).all()
    
    return {
        "invoices": [
            {
                "id": inv.id,
                "stripe_invoice_id": inv.stripe_invoice_id,
                "amount": inv.total_amount / 100,
                "status": inv.stripe_status,
                "period_start": inv.period_start,
                "period_end": inv.period_end,
                "paid": inv.paid,
                "pdf_url": inv.invoice_pdf_url,
            }
            for inv in invoices
        ],
        "total": session.query(Invoice).filter(
            Invoice.user_id == user.id
        ).count(),
    }


@billing_router.get("/usage")
async def get_usage_info(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Get current usage and cost."""
    
    subscription = session.query(Subscription).filter(
        Subscription.user_id == user.id
    ).first()
    
    if not subscription:
        return {
            "tier": "free",
            "usage_characters": 0,
            "usage_cost": 0,
            "remaining_quota": user.monthly_synthesis_quota,
            "cost_per_million_chars": 0,
        }
    
    # Calculate remaining quota
    remaining = subscription.monthly_character_limit - user.current_month_usage_characters
    
    # Calculate current month cost
    current_cost = user.current_month_cost / 100
    
    from app.core.config import get_settings
    settings = get_settings()
    pricing = settings.STRIPE_PRICING[subscription.tier]
    
    return {
        "tier": subscription.tier,
        "usage_characters": user.current_month_usage_characters,
        "remaining_quota": remaining,
        "usage_cost": current_cost,
        "monthly_charge": subscription.monthly_price_cents / 100,
        "cost_per_million_chars": pricing["overage_price_per_million_cents"] / 100,
        "period_end": subscription.current_period_end,
    }


# Register router
app.include_router(billing_router)


# ============ Webhooks ============

@app.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    session: Session = Depends(get_session),
):
    """Handle Stripe webhooks."""
    
    body = await request.body()
    signature = request.headers.get("stripe-signature")
    
    if not signature:
        raise HTTPException(status_code=400, detail="Missing stripe-signature")
    
    try:
        event = BillingManager.verify_webhook_signature(body.decode(), signature)
    except Exception as e:
        logger.error(f"Webhook verification failed: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    billing_mgr = get_billing_manager()
    
    # Handle different event types
    if event["type"] == "customer.subscription.updated":
        billing_mgr.handle_subscription_updated(
            event["data"]["object"]["id"],
            session
        )
    
    elif event["type"] == "customer.subscription.deleted":
        billing_mgr.handle_subscription_updated(
            event["data"]["object"]["id"],
            session
        )
    
    elif event["type"] == "invoice.payment_succeeded":
        billing_mgr.handle_invoice_payment_succeeded(
            event["data"]["object"]["id"],
            session
        )
    
    elif event["type"] == "invoice.payment_failed":
        logger.warning(f"Payment failed for invoice {event['data']['object']['id']}")
        # TODO: Send email notification
    
    return {"status": "received"}
```

---

## Step 5: Integrate with Synthesis

Update synthesis endpoint to record usage:

```python
# In app/main.py synthesize endpoint, after creating job:

from app.services.billing import get_billing_manager

# ... create synthesis job ...

# Record usage for billing
billing_mgr = get_billing_manager()
billing_mgr.record_usage(
    user=user,
    quantity=len(request.text),
    session=session,
    description=f"Synthesis: {request.text[:50]}..."
)

# Update user's monthly usage
user.current_month_usage_characters += len(request.text)
session.add(user)
session.commit()
```

---

## Step 6: Environment Variables

```bash
# .env

# Stripe
STRIPE_API_KEY=<your_stripe_secret_key>  # Get from Stripe Dashboard
STRIPE_WEBHOOK_SECRET=<your_stripe_webhook_secret>
STRIPE_PUBLISHABLE_KEY=<your_stripe_publishable_key>

# Or for production:
# STRIPE_API_KEY=sk_live_...
# STRIPE_WEBHOOK_SECRET=whsec_...
# STRIPE_PUBLISHABLE_KEY=pk_live_...
```

---

## Step 7: Database Migrations

```bash
# Create new Alembic migration
alembic revision --autogenerate -m "Add Stripe billing models"

# The migration will auto-detect:
# - Subscription table
# - Invoice table
# - UsageEvent table
# - User fields: stripe_customer_id, stripe_subscription_id, etc.

# Apply migration
alembic upgrade head
```

---

## Testing

### Test Stripe Integration

```python
# tests/test_billing.py

import pytest
from app.services.billing import BillingManager

@pytest.mark.asyncio
async def test_create_subscription(test_user, session):
    """Test subscription creation."""
    
    billing_mgr = BillingManager()
    result = billing_mgr.create_subscription(test_user, "starter", session)
    
    assert "subscription_id" in result
    assert result["status"] in ["active", "incomplete"]


@pytest.mark.asyncio
async def test_record_usage(test_user, session):
    """Test usage recording."""
    
    billing_mgr = BillingManager()
    billing_mgr.record_usage(test_user, 1000, session)
    
    assert test_user.current_month_usage_characters == 1000


@pytest.mark.asyncio
async def test_webhook_verification():
    """Test webhook signature verification."""
    
    # Create test event
    event = {"type": "ping"}
    signature = "fake_signature"
    
    with pytest.raises(Exception):
        BillingManager.verify_webhook_signature(
            json.dumps(event),
            signature
        )
```

### Use Stripe Test Cards

```
4242 4242 4242 4242 - Success
4000 0000 0000 0002 - Card decline
4000 0025 0000 3155 - 3D Secure required
```

---

## Deployment Checklist

- [ ] Create Stripe account
- [ ] Set up products and prices
- [ ] Create webhook endpoints in Stripe Dashboard
- [ ] Add Stripe API keys to environment
- [ ] Run database migrations
- [ ] Test webhook handling with Stripe CLI:
  ```bash
  stripe listen --forward-to http://localhost:8000/webhooks/stripe
  ```
- [ ] Deploy billing service
- [ ] Test subscription flow end-to-end
- [ ] Set up automated dunning rules in Stripe
- [ ] Configure email notifications
- [ ] Monitor webhook delivery

---

## Next Steps

After implementing Stripe:

1. **Webhook Retry Logic** - Handle failed webhook deliveries
2. **Email Notifications** - Send payment confirmations, invoices
3. **Dunning Management** - Automatic retry for failed payments
4. **Tax Calculation** - Integrate TaxJar for tax compliance
5. **Metering Optimization** - Batch usage events for efficiency

---

## Cost Estimate

- **Stripe fees:** 2.9% + $0.30 per transaction
- **For $50K MRR:** ~$1.5K/month in Stripe fees
- **ROI:** Trivial compared to payment processing risk

---

## Resources

- **Stripe Documentation:** https://stripe.com/docs
- **Stripe Billing:** https://stripe.com/docs/billing
- **Stripe Python SDK:** https://github.com/stripe/stripe-python
- **Test Mode:** Use sk_test_ keys for safe testing
