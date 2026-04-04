from sqlmodel import SQLModel, Field, Relationship
from datetime import datetime, timezone
from typing import Optional, List
import uuid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    """User account model."""
    __tablename__ = "users"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    email: str = Field(unique=True, index=True)
    username: str = Field(unique=True, index=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    is_premium: bool = Field(default=False)
    tier: str = Field(default="free")  # free, starter, pro, enterprise
    
    # API keys
    api_key: str = Field(unique=True, index=True, default_factory=lambda: str(uuid.uuid4()))
    
    # Quotas
    monthly_synthesis_quota: int = Field(default=100000)  # characters
    current_month_usage: int = Field(default=0)
    
    # Voice contribution tracking
    voices_contributed: int = Field(default=0)  # Voices shared to marketplace
    has_contributed_voice: bool = Field(default=False)  # At least one voice shared
    voice_consent_granted: bool = Field(default=False)  # Consent to use voice for training
    voice_consent_updated_at: Optional[datetime] = None
    
    # Referral/Free tier tracking
    current_free_period_end: Optional[datetime] = None  # When current free period ends
    free_periods_used: int = Field(default=0)  # Count of free periods earned
    
    # Stripe billing
    stripe_customer_id: Optional[str] = Field(default=None, unique=True, index=True)
    stripe_subscription_id: Optional[str] = Field(default=None, unique=True, index=True)
    subscription_status: str = Field(default="inactive")  # active, paused, canceled, past_due
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    current_month_usage_characters: int = Field(default=0)
    current_month_cost_cents: int = Field(default=0)
    total_spent_cents: int = Field(default=0)
    
    # Relationships
    voices: List["Voice"] = Relationship(back_populates="owner")
    synthesis_jobs: List["SynthesisJob"] = Relationship(back_populates="user")
    voice_contributions: List["VoiceContribution"] = Relationship(back_populates="user")
    free_trial_grants: List["FreeTrialGrant"] = Relationship(back_populates="user")
    subscriptions: List["Subscription"] = Relationship(back_populates="user")
    invoices: List["Invoice"] = Relationship(back_populates="user")
    usage_events: List["UsageEvent"] = Relationship(back_populates="user")
    
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Voice(SQLModel, table=True):
    """Custom voice model for voice cloning."""
    __tablename__ = "voices"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    owner_id: str = Field(foreign_key="users.id", index=True)
    
    name: str = Field(index=True)
    description: Optional[str] = None
    
    # Voice characteristics
    gender: Optional[str] = None  # male, female, neutral
    accent: Optional[str] = None  # e.g., American, British
    language: str = Field(default="en")
    
    # Voice embedding for cloning
    speaker_embedding: bytes  # Serialized numpy array
    embedding_model: str = Field(default="resemblyzer")
    
    # Reference audio
    sample_urls: Optional[str] = None  # JSON-encoded S3 URLs of reference samples
    
    # Metadata
    is_public: bool = Field(default=False)
    is_verified: bool = Field(default=False)
    quality_score: float = Field(default=0.0)  # 0-1 score based on samples
    
    # Usage stats
    total_characters_synthesized: int = Field(default=0)
    last_used_at: Optional[datetime] = None
    
    # Relationship
    owner: User = Relationship(back_populates="voices")
    
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SynthesisJob(SQLModel, table=True):
    """Text-to-speech synthesis job."""
    __tablename__ = "synthesis_jobs"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    voice_id: str = Field(foreign_key="voices.id", index=True)
    
    # Input
    text: str
    text_hash: str = Field(index=True)  # For caching
    
    # Parameters
    language: str = Field(default="en")
    style: Optional[str] = None  # normal, dramatic, whisper, etc.
    speed: float = Field(default=1.0)
    pitch: float = Field(default=1.0
    )
    
    # Status
    status: str = Field(default="pending", index=True)  # pending, processing, completed, failed
    progress: float = Field(default=0.0)  # 0-1
    
    # Output
    audio_url: Optional[str] = None  # S3 URL
    audio_duration: Optional[float] = None  # seconds
    
    # Caching
    cache_key: Optional[str] = None
    is_cached: bool = Field(default=False)
    
    # Error handling
    error_message: Optional[str] = None
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)
    
    # Performance metrics
    inference_time_ms: Optional[float] = None
    total_time_ms: Optional[float] = None
    
    # Relationships
    user: User = Relationship(back_populates="synthesis_jobs")
    
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None


class VoiceSample(SQLModel, table=True):
    """Reference audio sample for voice cloning training."""
    __tablename__ = "voice_samples"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    voice_id: str = Field(foreign_key="voices.id", index=True)
    
    # Audio file
    audio_url: str  # S3 URL
    duration: float  # seconds
    sample_rate: int
    
    # Metadata
    transcript: Optional[str] = None
    quality_score: float = Field(default=0.0)
    
    created_at: datetime = Field(default_factory=utc_now)


class VoiceContribution(SQLModel, table=True):
    """Track when users contribute voices to the marketplace for training."""
    __tablename__ = "voice_contributions"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    voice_id: str = Field(foreign_key="voices.id", index=True)
    
    # Contribution details
    voice_name: str  # Copy of voice name at contribution time
    description: Optional[str] = None
    
    # Consent
    consent_granted: bool = Field(default=True)
    consent_version: str = Field(default="1.0")  # Version of terms accepted
    
    # Usage
    times_used_in_training: int = Field(default=0)
    times_synthesized: int = Field(default=0)
    
    # Reward status
    free_period_awarded: bool = Field(default=False)
    free_period_grant_id: Optional[str] = Field(default=None, foreign_key="free_trial_grants.id")
    
    # Metadata
    status: str = Field(default="active")  # active, withdrawn, rejected
    rejection_reason: Optional[str] = None
    
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    
    # Relationships
    user: User = Relationship(back_populates="voice_contributions")


class FreeTrialGrant(SQLModel, table=True):
    """Track free trial periods granted to users who contribute voices."""
    __tablename__ = "free_trial_grants"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    
    # Trial period
    start_date: datetime
    end_date: datetime
    grant_reason: str  # "voice_contribution", "referral", "promo", etc
    related_voice_id: Optional[str] = None  # Voice that triggered this grant
    
    # Status
    status: str = Field(default="active")  # active, expired, redeemed
    is_active: bool = Field(default=True)
    
    # Quota boost
    bonus_monthly_quota: int = Field(default=1000000)  # Extra chars during trial
    
    created_at: datetime = Field(default_factory=utc_now)
    
    # Relationships
    user: User = Relationship(back_populates="free_trial_grants")


class APIKeyModel(SQLModel, table=True):
    """API keys for programmatic access."""
    __tablename__ = "api_keys"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    
    # Key (hashed)
    hashed_key: str = Field(unique=True, index=True)
    
    # Metadata
    label: str  # User-friendly name (e.g., "Production", "Testing")
    active: bool = Field(default=True)
    
    # Usage tracking
    last_used: Optional[datetime] = None
    requests_count: int = Field(default=0)
    
    created_at: datetime = Field(default_factory=utc_now)


class Subscription(SQLModel, table=True):
    """Stripe subscription for recurring billing."""
    __tablename__ = "subscriptions"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    
    # Stripe IDs
    stripe_subscription_id: str = Field(unique=True, index=True)
    stripe_customer_id: str = Field(index=True)
    stripe_price_id: str  # Product->Price mapping
    
    # Subscription details
    tier: str  # starter, pro, enterprise
    status: str = Field(default="active")  # active, past_due, paused, canceled
    
    # Billing cycle
    current_period_start: datetime
    current_period_end: datetime
    renewal_attempts: int = Field(default=0)
    
    # Pricing
    monthly_character_limit: int  # How many characters included
    monthly_price_cents: int  # Base subscription price
    
    # Features
    auto_renew: bool = Field(default=True)
    
    # Relationships
    user: User = Relationship(back_populates="subscriptions")
    invoices: List["Invoice"] = Relationship(back_populates="subscription")
    
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    canceled_at: Optional[datetime] = None


class Invoice(SQLModel, table=True):
    """Monthly invoices for subscriptions."""
    __tablename__ = "invoices"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    subscription_id: str = Field(foreign_key="subscriptions.id")
    
    # Stripe reference
    stripe_invoice_id: str = Field(unique=True, index=True)
    stripe_status: str = Field(default="draft")  # draft, open, paid, uncollectible, void
    
    # Amount breakdown (in cents)
    base_amount: int  # Subscription base price
    usage_amount: int = Field(default=0)  # Overage charges
    tax_amount: int = Field(default=0)
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
    
    created_at: datetime = Field(default_factory=utc_now)


class UsageEvent(SQLModel, table=True):
    """Usage events for metered billing."""
    __tablename__ = "usage_events"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    invoice_id: Optional[str] = Field(default=None, foreign_key="invoices.id")
    synthesis_job_id: Optional[str] = None
    
    # Stripe metering
    stripe_subscription_item_id: Optional[str] = None
    
    # Usage details
    event_type: str  # synthesis_character, voice_clone, api_call
    quantity: int  # How many chars/clones/calls
    unit_price_cents: int = Field(default=0)  # Price per unit
    total_cost_cents: int = Field(default=0)  # quantity * unit_price_cents
    
    # Metadata
    description: str  # Human readable: "1000 characters synthesized"
    
    created_at: datetime = Field(default_factory=utc_now)
    
    # Relationships
    user: User = Relationship(back_populates="usage_events")
    invoice: Optional[Invoice] = Relationship(back_populates="usage_events")
