"""
Stripe billing integration service.

Handles:
- Customer management (creation, updates)
- Subscription creation/cancellation
- Usage metering
- Invoice generation
- Webhook handling
"""

import stripe
import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Tuple
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models.db import User, Subscription, Invoice, UsageEvent

logger = logging.getLogger(__name__)
settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

# Configure Stripe
if settings.STRIPE_API_KEY:
    stripe.api_key = settings.STRIPE_API_KEY


class StripeError(Exception):
    """Raised when Stripe operations fail."""
    pass


class BillingManager:
    """Manage Stripe billing operations."""
    
    def __init__(self):
        if not settings.STRIPE_API_KEY:
            logger.warning("Stripe API key not configured")
        self.pricing = settings.STRIPE_PRICING
    
    # ============ Customer Management ============
    
    def create_stripe_customer(
        self,
        user: User,
        session: Session,
    ) -> str:
        """Create or retrieve Stripe customer."""
        
        if user.stripe_customer_id:
            logger.debug(f"User {user.id} already has Stripe customer {user.stripe_customer_id}")
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
    ) -> Dict:
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
            user.current_period_start = datetime.fromtimestamp(subscription.current_period_start)
            user.current_period_end = datetime.fromtimestamp(subscription.current_period_end)
            session.add(user)
            session.commit()
            
            logger.info(f"Created subscription {subscription.id} for user {user.id} on tier {tier}")
            
            return {
                "subscription_id": subscription.id,
                "status": subscription.status,
                "client_secret": (
                    subscription.latest_invoice.payment_intent.client_secret
                    if subscription.status == "incomplete" and subscription.latest_invoice
                    else None
                ),
                "tier": tier,
            }
        
        except stripe.error.StripeError as e:
            logger.error(f"Failed to create subscription: {str(e)}")
            raise StripeError(f"Failed to create subscription: {str(e)}")
    
    def update_subscription_tier(
        self,
        user: User,
        new_tier: str,
        session: Session,
    ) -> Dict:
        """Upgrade or downgrade subscription tier."""
        
        if not user.stripe_subscription_id:
            raise ValueError("User has no active subscription")
        
        if new_tier not in self.pricing:
            raise ValueError(f"Invalid tier: {new_tier}")
        
        pricing = self.pricing[new_tier]
        
        try:
            # Get current subscription
            subscription = stripe.Subscription.retrieve(user.stripe_subscription_id)
            
            # Update subscription item
            stripe.Subscription.modify(
                user.stripe_subscription_id,
                items=[
                    {
                        "id": subscription.items.data[0].id,
                        "price": pricing["price_id"],
                    }
                ],
                metadata={"tier": new_tier}
            )
            
            # Update database
            db_sub = session.exec(
                select(Subscription).where(Subscription.stripe_subscription_id == user.stripe_subscription_id)
            ).first()
            
            if db_sub:
                db_sub.tier = new_tier
                db_sub.stripe_price_id = pricing["price_id"]
                db_sub.monthly_character_limit = pricing["character_limit"]
                db_sub.monthly_price_cents = pricing["monthly_cents"]
                session.add(db_sub)
            
            user.tier = new_tier
            session.add(user)
            session.commit()
            
            logger.info(f"Updated subscription for user {user.id} to tier {new_tier}")
            
            return {
                "subscription_id": subscription.id,
                "tier": new_tier,
                "status": "updated",
            }
        
        except stripe.error.StripeError as e:
            logger.error(f"Failed to update subscription: {str(e)}")
            raise StripeError(f"Failed to update subscription: {str(e)}")
    
    def cancel_subscription(
        self,
        user: User,
        session: Session,
        at_period_end: bool = True,
    ) -> Dict:
        """Cancel user's subscription."""
        
        if not user.stripe_subscription_id:
            raise ValueError("User has no active subscription")
        
        try:
            subscription = stripe.Subscription.delete(
                user.stripe_subscription_id,
                invoice_now=not at_period_end,
            )
            
            # Update database
            db_sub = session.exec(
                select(Subscription).where(Subscription.stripe_subscription_id == user.stripe_subscription_id)
            ).first()
            
            if db_sub:
                db_sub.status = "canceled"
                db_sub.canceled_at = utc_now()
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
        synthesis_job_id: Optional[str] = None,
    ) -> None:
        """Record usage event for metering."""
        
        try:
            # Create usage event in database (applies whether user has subscription or not)
            usage_event = UsageEvent(
                user_id=user.id,
                synthesis_job_id=synthesis_job_id,
                stripe_subscription_item_id="" if user.stripe_subscription_id else None,
                event_type="synthesis_character",
                quantity=quantity,
                unit_price_cents=0,
                total_cost_cents=0,
                description=description,
            )
            session.add(usage_event)
            
            # Update user's usage
            user.current_month_usage_characters += quantity
            session.add(user)
            session.commit()
            
            logger.debug(f"Recorded {quantity} char usage for user {user.id}")
        
        except Exception as e:
            logger.error(f"Failed to record usage: {str(e)}")
    
    # ============ Invoice Management ============
    
    def get_upcoming_invoice(
        self,
        user: User,
    ) -> Optional[Dict]:
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
                        "description": line.description or "TTS Service",
                        "amount": line.amount / 100,
                        "quantity": line.quantity or 1,
                    }
                    for line in upcoming.lines
                ]
            }
        
        except stripe.error.InvalidRequestError:
            logger.debug(f"No upcoming invoice for user {user.id}")
            return None
        except stripe.error.StripeError as e:
            logger.error(f"Failed to get upcoming invoice: {str(e)}")
            return None
    
    def sync_invoice_from_stripe(
        self,
        stripe_invoice_id: str,
        session: Session,
    ) -> Optional[str]:
        """Fetch invoice from Stripe and sync to database."""
        
        try:
            stripe_invoice = stripe.Invoice.retrieve(stripe_invoice_id)
            
            # Find or create invoice in database
            db_invoice = session.exec(
                select(Invoice).where(Invoice.stripe_invoice_id == stripe_invoice_id)
            ).first()
            
            if not db_invoice:
                # Get subscription
                db_sub = session.exec(
                    select(Subscription).where(Subscription.stripe_subscription_id == stripe_invoice.subscription)
                ).first()
                
                if not db_sub:
                    logger.error(f"Subscription not found for invoice {stripe_invoice_id}")
                    return None
                
                db_invoice = Invoice(
                    user_id=db_sub.user_id,
                    subscription_id=db_sub.id,
                    stripe_invoice_id=stripe_invoice_id,
                    stripe_status=stripe_invoice.status,
                    base_amount=stripe_invoice.subtotal or 0,
                    usage_amount=0,
                    tax_amount=stripe_invoice.tax or 0,
                    total_amount=stripe_invoice.total or 0,
                    period_start=datetime.fromtimestamp(stripe_invoice.period_start),
                    period_end=datetime.fromtimestamp(stripe_invoice.period_end),
                    due_date=datetime.fromtimestamp(stripe_invoice.due_date) if stripe_invoice.due_date else None,
                    invoice_pdf_url=stripe_invoice.pdf,
                    paid=stripe_invoice.paid,
                    paid_at=datetime.fromtimestamp(stripe_invoice.paid_at) if stripe_invoice.paid_at else None,
                )
                session.add(db_invoice)
            else:
                # Update existing invoice
                db_invoice.stripe_status = stripe_invoice.status
                db_invoice.paid = stripe_invoice.paid
                if stripe_invoice.paid_at:
                    db_invoice.paid_at = datetime.fromtimestamp(stripe_invoice.paid_at)
                session.add(db_invoice)
            
            session.commit()
            logger.info(f"Synced invoice {stripe_invoice_id}")
            return db_invoice.id
        
        except stripe.error.StripeError as e:
            logger.error(f"Failed to sync invoice: {str(e)}")
            return None
    
    # ============ Webhook Handling ============
    
    @staticmethod
    def verify_webhook_signature(body: str, signature: str) -> Dict:
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
        
        try:
            subscription = stripe.Subscription.retrieve(stripe_subscription_id)
            
            # Update database
            db_sub = session.exec(
                select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
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
                
                # Also update user
                user = db_sub.user
                user.subscription_status = subscription.status
                user.current_period_start = db_sub.current_period_start
                user.current_period_end = db_sub.current_period_end
                session.add(user)
                
                session.commit()
                logger.info(f"Updated subscription {stripe_subscription_id} status to {subscription.status}")
        
        except Exception as e:
            logger.error(f"Failed to handle subscription updated: {str(e)}")
    
    @staticmethod
    def handle_subscription_deleted(
        stripe_subscription_id: str,
        session: Session,
    ) -> None:
        """Handle subscription.deleted webhook."""
        
        try:
            db_sub = session.exec(
                select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
            ).first()
            
            if db_sub:
                db_sub.status = "canceled"
                db_sub.canceled_at = utc_now()
                session.add(db_sub)
                
                user = db_sub.user
                user.subscription_status = "canceled"
                user.stripe_subscription_id = None
                user.tier = "free"
                session.add(user)
                
                session.commit()
                logger.info(f"Deleted subscription {stripe_subscription_id}")
        
        except Exception as e:
            logger.error(f"Failed to handle subscription deleted: {str(e)}")
    
    @staticmethod
    def handle_invoice_payment_succeeded(
        stripe_invoice_id: str,
        session: Session,
    ) -> None:
        """Handle invoice.payment_succeeded webhook."""
        
        try:
            invoice = stripe.Invoice.retrieve(stripe_invoice_id)
            
            # Find or create invoice in database
            db_invoice = session.exec(
                select(Invoice).where(Invoice.stripe_invoice_id == stripe_invoice_id)
            ).first()
            
            if db_invoice:
                db_invoice.paid = True
                db_invoice.paid_at = utc_now()
                db_invoice.stripe_status = invoice.status
                session.add(db_invoice)
                session.commit()
                logger.info(f"Invoice {stripe_invoice_id} marked as paid")
            else:
                # Sync invoice if not in DB
                BillingManager.sync_invoice_from_stripe(stripe_invoice_id, session)
        
        except Exception as e:
            logger.error(f"Failed to handle invoice payment succeeded: {str(e)}")
    
    @staticmethod
    def handle_invoice_payment_failed(
        stripe_invoice_id: str,
        session: Session,
    ) -> None:
        """Handle invoice.payment_failed webhook."""
        
        try:
            logger.warning(f"Payment failed for invoice {stripe_invoice_id}")
            
            db_invoice = session.exec(
                select(Invoice).where(Invoice.stripe_invoice_id == stripe_invoice_id)
            ).first()
            
            if db_invoice:
                db_invoice.stripe_status = "open"  # Will retry
                session.add(db_invoice)
                session.commit()
        
        except Exception as e:
            logger.error(f"Failed to handle invoice payment failed: {str(e)}")


def get_billing_manager() -> BillingManager:
    """Dependency injection for BillingManager."""
    return BillingManager()
