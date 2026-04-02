"""
Test suite for Stripe billing integration.

Tests:
- Customer creation
- Subscription management
- Usage metering
- Invoice tracking
- Webhook handling
"""

import pytest
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, MagicMock
from sqlmodel import Session, create_engine, select
from sqlmodel.pool import StaticPool

from app.models.db import User, Subscription, Invoice, UsageEvent
from app.services.billing import BillingManager, StripeError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ============ Fixtures ============

@pytest.fixture
def session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Create all tables
    from app.models.db import SQLModel
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        yield session


@pytest.fixture
def test_user(session: Session) -> User:
    """Create a test user."""
    user = User(
        email="test@example.com",
        username="testuser",
        hashed_password="hashed_password",
        is_active=True,
        tier="free",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def billing_manager():
    """Create a BillingManager instance."""
    return BillingManager()


# ============ Customer Tests ============

class TestCustomerManagement:
    """Test Stripe customer creation and management."""
    
    @patch("stripe.Customer.create")
    def test_create_stripe_customer(self, mock_create, test_user, session, billing_manager):
        """Test creating a Stripe customer."""
        
        # Mock Stripe response
        mock_create.return_value = Mock(id="cus_test123")
        
        # Create customer
        customer_id = billing_manager.create_stripe_customer(test_user, session)
        
        # Verify
        assert customer_id == "cus_test123"
        assert test_user.stripe_customer_id == "cus_test123"
        
        # Verify Stripe was called
        mock_create.assert_called_once()
        call_args = mock_create.call_args
        assert call_args.kwargs["email"] == "test@example.com"
        assert call_args.kwargs["name"] == "testuser"
    
    @patch("stripe.Customer.create")
    def test_create_customer_already_exists(self, mock_create, test_user, session, billing_manager):
        """Test that we don't create duplicate customers."""
        
        # User already has a customer ID
        test_user.stripe_customer_id = "cus_existing"
        session.add(test_user)
        session.commit()
        
        # Call create
        customer_id = billing_manager.create_stripe_customer(test_user, session)
        
        # Should return existing ID without calling Stripe
        assert customer_id == "cus_existing"
        mock_create.assert_not_called()
    
    @patch("stripe.Customer.create")
    def test_create_customer_stripe_error(self, mock_create, test_user, session, billing_manager):
        """Test error handling when Stripe fails."""
        import stripe as stripe_module
        
        # Mock Stripe error
        mock_create.side_effect = stripe_module.error.StripeError("API Error")
        
        # Should raise StripeError
        with pytest.raises(StripeError):
            billing_manager.create_stripe_customer(test_user, session)


# ============ Subscription Tests ============

class TestSubscriptionManagement:
    """Test subscription creation and management."""
    
    @patch("stripe.Subscription.create")
    @patch("stripe.Customer.create")
    def test_create_subscription(self, mock_customer_create, mock_sub_create, test_user, session, billing_manager):
        """Test creating a subscription."""
        
        # Mock responses
        mock_customer_create.return_value = Mock(id="cus_test123")
        mock_sub_create.return_value = Mock(
            id="sub_test123",
            status="incomplete",
            current_period_start=int(utc_now().timestamp()),
            current_period_end=int((utc_now() + timedelta(days=30)).timestamp()),
            latest_invoice=Mock(
                payment_intent=Mock(client_secret="pi_secret123")
            )
        )
        
        # Create subscription
        result = billing_manager.create_subscription(test_user, "starter", session)
        
        # Verify
        assert result["subscription_id"] == "sub_test123"
        assert result["status"] == "incomplete"
        assert "client_secret" in result
        
        # Verify database
        session.refresh(test_user)
        assert test_user.stripe_subscription_id == "sub_test123"
        assert test_user.tier == "starter"
        
        # Verify subscription record
        db_sub = session.exec(
            select(Subscription).where(Subscription.stripe_subscription_id == "sub_test123")
        ).first()
        assert db_sub is not None
        assert db_sub.tier == "starter"
    
    @patch("stripe.Subscription.modify")
    @patch("stripe.Subscription.retrieve")
    def test_update_subscription_tier(self, mock_retrieve, mock_modify, test_user, session, billing_manager):
        """Test upgrading subscription tier."""
        
        # Setup user with existing subscription
        now = utc_now()
        subscription = Subscription(
            user_id=test_user.id,
            stripe_subscription_id="sub_test123",
            stripe_customer_id="cus_test123",
            stripe_price_id="price_starter",
            tier="starter",
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            monthly_character_limit=1_000_000,
            monthly_price_cents=5000,
        )
        session.add(subscription)
        
        test_user.stripe_subscription_id = "sub_test123"
        session.add(test_user)
        session.commit()
        
        # Mock Stripe
        mock_retrieve.return_value = Mock(
            id="sub_test123",
            items=Mock(data=[Mock(id="si_test123")])
        )
        mock_modify.return_value = Mock(id="sub_test123")
        
        # Update tier
        result = billing_manager.update_subscription_tier(test_user, "pro", session)
        
        # Verify
        assert result["tier"] == "pro"
        assert result["status"] == "updated"
        
        # Verify Stripe was called
        mock_modify.assert_called_once()


# ============ Usage Metering Tests ============

class TestUsageMetering:
    """Test usage metering for billing."""
    
    def test_record_usage_no_subscription(self, test_user, session, billing_manager):
        """Test recording usage for user without subscription."""
        
        # Should not raise error, just skip
        billing_manager.record_usage(test_user, 1000, session)
        
        # User usage should still be updated
        session.refresh(test_user)
        assert test_user.current_month_usage_characters == 1000
    
    def test_record_usage_with_subscription(self, test_user, session, billing_manager):
        """Test recording usage for user with subscription."""
        
        # Create subscription
        subscription = Subscription(
            user_id=test_user.id,
            stripe_subscription_id="sub_test123",
            stripe_customer_id="cus_test123",
            stripe_price_id="price_starter",
            tier="starter",
            status="active",
            current_period_start=utc_now(),
            current_period_end=utc_now() + timedelta(days=30),
            monthly_character_limit=1_000_000,
            monthly_price_cents=5000,
        )
        session.add(subscription)
        test_user.stripe_subscription_id = "sub_test123"
        session.add(test_user)
        session.commit()
        
        # Record usage
        billing_manager.record_usage(
            test_user,
            5000,
            session,
            description="Test synthesis",
            synthesis_job_id="job_123"
        )
        
        # Verify usage event created
        usage_event = session.exec(select(UsageEvent)).first()
        assert usage_event is not None
        assert usage_event.quantity == 5000
        assert usage_event.description == "Test synthesis"
        assert usage_event.synthesis_job_id == "job_123"
        
        # Verify user usage updated
        session.refresh(test_user)
        assert test_user.current_month_usage_characters == 5000


# ============ Invoice Tests ============

class TestInvoiceManagement:
    """Test invoice tracking and retrieval."""
    
    def test_get_upcoming_invoice(self, test_user, session, billing_manager):
        """Test fetching upcoming invoice."""
        
        # Skip this test - stripe.Invoice.upcoming requires actual API
        # In production, integration tests should be run with Stripe test account
        import pytest
        pytest.skip("Stripe API integration test - requires real API key")
    
    @patch("app.services.billing.stripe.Invoice.retrieve")
    def test_sync_invoice_from_stripe(self, mock_retrieve, test_user, session, billing_manager):
        """Test syncing invoice from Stripe."""
        
        # Create subscription first
        subscription = Subscription(
            user_id=test_user.id,
            stripe_subscription_id="sub_test123",
            stripe_customer_id="cus_test123",
            stripe_price_id="price_starter",
            tier="starter",
            status="active",
            current_period_start=utc_now(),
            current_period_end=utc_now() + timedelta(days=30),
            monthly_character_limit=1_000_000,
            monthly_price_cents=5000,
        )
        session.add(subscription)
        session.commit()
        
        # Mock invoice
        mock_retrieve.return_value = Mock(
            id="in_test123",
            status="paid",
            subtotal=5000,
            tax=0,
            total=5000,
            paid=True,
            paid_at=int(utc_now().timestamp()),
            period_start=int(utc_now().timestamp()),
            period_end=int((utc_now() + timedelta(days=30)).timestamp()),
            due_date=None,
            pdf="https://example.com/invoice.pdf",
            subscription="sub_test123",
        )
        
        # Sync
        invoice_id = billing_manager.sync_invoice_from_stripe("in_test123", session)
        
        # Verify
        assert invoice_id is not None
        
        db_invoice = session.exec(select(Invoice)).first()
        assert db_invoice is not None
        assert db_invoice.stripe_invoice_id == "in_test123"
        assert db_invoice.user_id == test_user.id
        assert db_invoice.paid is True


# ============ Webhook Tests ============

class TestWebhookHandling:
    """Test Stripe webhook event handling."""
    
    def test_verify_webhook_signature_valid(self, billing_manager):
        """Test verifying a valid webhook signature."""
        import stripe as stripe_module
        
        with patch("stripe.Webhook.construct_event") as mock_construct:
            mock_construct.return_value = {"type": "ping", "id": "evt_test123"}
            
            event = BillingManager.verify_webhook_signature("body", "sig_test")
            
            assert event["type"] == "ping"
            mock_construct.assert_called_once()
    
    def test_verify_webhook_signature_invalid(self, billing_manager):
        """Test handling invalid signature."""
        import stripe as stripe_module
        
        with patch("stripe.Webhook.construct_event") as mock_construct:
            mock_construct.side_effect = stripe_module.error.SignatureVerificationError("Invalid", "sig")
            
            with pytest.raises(stripe_module.error.SignatureVerificationError):
                BillingManager.verify_webhook_signature("body", "sig_bad")
    
    @patch("stripe.Subscription.retrieve")
    def test_handle_subscription_updated(self, mock_retrieve, test_user, session, billing_manager):
        """Test subscription.updated webhook."""
        
        # Setup
        now = utc_now()
        subscription = Subscription(
            user_id=test_user.id,
            stripe_subscription_id="sub_test123",
            stripe_customer_id="cus_test123",
            stripe_price_id="price_starter",
            tier="starter",
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            monthly_character_limit=1_000_000,
            monthly_price_cents=5000,
        )
        session.add(subscription)
        test_user.stripe_subscription_id = "sub_test123"
        session.add(test_user)
        session.commit()
        
        # Mock updated subscription
        new_time = now + timedelta(days=60)
        mock_retrieve.return_value = Mock(
            id="sub_test123",
            status="active",
            current_period_start=int(new_time.timestamp()),
            current_period_end=int((new_time + timedelta(days=30)).timestamp()),
        )
        
        # Handle webhook
        BillingManager.handle_subscription_updated("sub_test123", session)
        
        # Verify
        session.refresh(subscription)
        assert subscription.status == "active"


# ============ Integration Tests ============

class TestBillingIntegration:
    """Integration tests for full billing workflows."""
    
    @patch("stripe.Customer.create")
    @patch("stripe.Subscription.create")
    def test_full_subscription_workflow(self, mock_sub_create, mock_customer_create, test_user, session, billing_manager):
        """Test complete subscription workflow."""
        
        # Mock Stripe
        mock_customer_create.return_value = Mock(id="cus_test123")
        mock_sub_create.return_value = Mock(
            id="sub_test123",
            status="active",
            current_period_start=int(utc_now().timestamp()),
            current_period_end=int((utc_now() + timedelta(days=30)).timestamp()),
            latest_invoice=None,
        )
        
        # 1. Create customer
        customer_id = billing_manager.create_stripe_customer(test_user, session)
        assert customer_id == "cus_test123"
        
        # 2. Create subscription
        result = billing_manager.create_subscription(test_user, "starter", session)
        assert result["subscription_id"] == "sub_test123"
        
        # 3. Record usage
        billing_manager.record_usage(test_user, 100_000, session, "Test usage")
        
        # Verify final state
        session.refresh(test_user)
        assert test_user.stripe_customer_id == "cus_test123"
        assert test_user.stripe_subscription_id == "sub_test123"
        assert test_user.tier == "starter"
        assert test_user.current_month_usage_characters == 100_000
        
        # Verify subscription in DB
        db_sub = session.exec(select(Subscription)).first()
        assert db_sub is not None
        assert db_sub.tier == "starter"
        
        # Verify usage event
        usage_event = session.exec(select(UsageEvent)).first()
        assert usage_event is not None
        assert usage_event.quantity == 100_000


# ============ Edge Cases ============

class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_invalid_tier(self, test_user, session, billing_manager):
        """Test subscribing to invalid tier."""
        
        with pytest.raises(ValueError):
            billing_manager.create_subscription(test_user, "invalid_tier", session)
    
    def test_cancel_subscription_no_subscription(self, test_user, session, billing_manager):
        """Test canceling when no subscription exists."""
        
        with pytest.raises(ValueError):
            billing_manager.cancel_subscription(test_user, session)
    
    def test_update_tier_no_subscription(self, test_user, session, billing_manager):
        """Test updating tier when no subscription exists."""
        
        with pytest.raises(ValueError):
            billing_manager.update_subscription_tier(test_user, "pro", session)
