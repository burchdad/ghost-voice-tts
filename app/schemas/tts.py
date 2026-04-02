from pydantic import BaseModel, Field, EmailStr, HttpUrl
from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum


class LanguageEnum(str, Enum):
    EN = "en"
    ES = "es"
    FR = "fr"
    DE = "de"
    IT = "it"
    PT = "pt"
    JA = "ja"
    KO = "ko"
    ZH = "zh"
    RU = "ru"


class StyleEnum(str, Enum):
    NORMAL = "normal"
    DRAMATIC = "dramatic"
    WHISPER = "whisper"
    UPBEAT = "upbeat"
    CALM = "calm"


class EmotionCurveEnum(str, Enum):
    STATIC = "static"
    RISE = "rise"
    FALL = "fall"
    ARC = "arc"
    WAVE = "wave"


class ProsodyTemplateEnum(str, Enum):
    """Named prosody presets that expand to a full set of emotion parameters.

    When set, the template overrides the individual emotion fields.
    """
    SALES_CALL      = "sales_call"
    STORYTELLING    = "storytelling"
    EXECUTIVE_BRIEF = "executive_brief"
    RAVEN_MODE      = "raven_mode"
    EMPATHY_SUPPORT = "empathy_support"
    URGENT_ALERT    = "urgent_alert"
    TRUSTED_ADVISOR = "trusted_advisor"
    HYPE_MODE       = "hype_mode"


class SynthesisModeEnum(str, Enum):
    """
    Latency tier for a synthesis request.

    realtime     – ultra-low latency (~200–400 ms), routed to fastest provider.
                   Ideal for live conversational voice agents.
    balanced     – balanced quality/latency (~1–2 s).  Default for most requests.
    high_quality – maximum quality, slower (3–10 s).  Use for recorded output,
                   voiceovers, etc.
    """
    REALTIME = "realtime"
    BALANCED = "balanced"
    HIGH_QUALITY = "high_quality"


class TemplateCompositionItem(BaseModel):
    """One entry in a multi-template composition request."""
    name: ProsodyTemplateEnum
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    axes: Optional[Dict[str, float]] = Field(
        default=None,
        description="Per-template axis overrides (e.g. {\"urgency\": 0.8}).",
    )


class WordEmphasisConfig(BaseModel):
    """Manual word-level emphasis override."""
    word_index: int = Field(..., ge=0, description="0-based word index in the text.")
    energy_boost: float = Field(default=1.35, ge=1.0, le=2.0)
    pitch_semitones: float = Field(default=1.0, ge=-6.0, le=6.0)


class HierarchicalProsodyConfig(BaseModel):
    """Word/pause-level prosody control (layered on top of sentence template)."""
    word_emphases: List[WordEmphasisConfig] = Field(default_factory=list)
    auto_emphasis: bool = Field(default=True, description="Auto-detect ALL_CAPS and *starred* words.")
    auto_pauses: bool = Field(default=True, description="Inject pauses from punctuation.")
    pause_scale: float = Field(default=1.0, ge=0.0, le=3.0, description="Multiply all pause durations.")


class StatusEnum(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CACHED = "cached"


# ============ User Schemas ============

class UserBase(BaseModel):
    email: EmailStr
    username: str


class UserCreate(UserBase):
    password: str


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None


class UserResponse(UserBase):
    id: str
    is_active: bool
    is_premium: bool
    api_key: str
    monthly_synthesis_quota: int
    current_month_usage: int
    created_at: datetime
    
    model_config = {"from_attributes": True}


# ============ Voice Schemas ============

class VoiceBase(BaseModel):
    name: str
    description: Optional[str] = None
    gender: Optional[str] = None
    accent: Optional[str] = None
    language: LanguageEnum = LanguageEnum.EN


class VoiceCreate(VoiceBase):
    pass


class VoiceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None


class VoiceResponse(VoiceBase):
    id: str
    owner_id: str
    is_public: bool
    is_verified: bool
    quality_score: float
    total_characters_synthesized: int
    last_used_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


# ============ Voice Cloning Schemas ============

class VoiceCloningRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    gender: Optional[str] = None
    accent: Optional[str] = None
    language: LanguageEnum = LanguageEnum.EN


class VoiceUploadRequest(BaseModel):
    voice_id: str


# ============ Synthesis Schemas ============

class SynthesisRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    voice_id: str
    language: LanguageEnum = LanguageEnum.EN
    style: StyleEnum = StyleEnum.NORMAL
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: float = Field(default=1.0, ge=0.5, le=2.0)
    stream: bool = Field(default=False)
    # ── Emotion modulation ────────────────────────────────────────────────────
    emotion: Optional[str] = Field(
        default=None,
        description=(
            "Emotion to apply to synthesis: neutral, excited, urgent, angry, calm, sad, "
            "whisper, confident, concerned, playful. "
            "Modulates pitch, speed, tone, and prosody dynamically."
        ),
    )
    secondary_emotion: Optional[str] = Field(
        default=None,
        description="Optional secondary emotion to blend with primary emotion.",
    )
    emotion_blend: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Blend ratio for secondary emotion. 0.0=primary only, 1.0=secondary only.",
    )
    emotion_intensity: float = Field(
        default=1.0,
        ge=0.0,
        le=2.0,
        description="Global intensity scale for emotional modulation.",
    )
    emotion_curve: EmotionCurveEnum = Field(
        default=EmotionCurveEnum.ARC,
        description="Temporal contour of emotion over the utterance.",
    )
    prosody_template: Optional[ProsodyTemplateEnum] = Field(
        default=None,
        description=(
            "Named prosody preset. When set, overrides emotion, secondary_emotion, "
            "emotion_blend, emotion_intensity, and emotion_curve with the preset values. "
            "Available: sales_call, storytelling, executive_brief, raven_mode, "
            "empathy_support, urgent_alert, trusted_advisor, hype_mode."
        ),
    )
    prosody_template_axes: Optional[Dict[str, float]] = Field(
        default=None,
        description=(
            "Axis overrides for prosody_template parameterization "
            "(e.g. {\"urgency\": 0.8, \"warmth\": 0.3}). Ignored when prosody_template is unset."
        ),
    )
    template_composition: Optional[List[TemplateCompositionItem]] = Field(
        default=None,
        description=(
            "Multi-template blend. When set, overrides prosody_template and emotion fields. "
            "Templates are blended by their normalised weights."
        ),
    )
    auto_template: bool = Field(
        default=False,
        description=(
            "When True, automatically select a prosody template from the input text. "
            "Only applied if template_composition and prosody_template are both unset."
        ),
    )
    hierarchical_prosody: Optional[HierarchicalProsodyConfig] = Field(
        default=None,
        description="Word/pause-level prosody layered on top of the resolved template.",
    )
    ml_prosody_refinement: bool = Field(
        default=True,
        description="Enable ML-assisted (heuristic baseline) prosody refinement.",
    )
    phoneme_alignment: bool = Field(
        default=True,
        description="Enable syllable/phoneme alignment hooks for fine-grained modulation.",
    )
    # ── Latency tier ──────────────────────────────────────────────────────────
    mode: SynthesisModeEnum = Field(
        default=SynthesisModeEnum.BALANCED,
        description="Synthesis latency tier: realtime | balanced | high_quality",
    )
    # ── Session / voice continuity ────────────────────────────────────────────
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "Opaque session identifier.  When supplied, voice settings are locked "
            "for the lifetime of the session so output stays consistent across turns."
        ),
    )
    voice_seed: Optional[int] = Field(
        default=None,
        description=(
            "Integer seed for deterministic voice generation.  "
            "The same seed + voice_id always produces the same tone profile."
        ),
    )


class SynthesisResponse(BaseModel):
    id: str
    status: StatusEnum
    audio_url: Optional[str] = None
    audio_duration: Optional[float] = None
    progress: float
    created_at: datetime
    completed_at: Optional[datetime] = None
    inference_time_ms: Optional[float] = None
    # ── Latency tier echo ─────────────────────────────────────────────────────
    mode: Optional[SynthesisModeEnum] = None
    # ── Session continuity ────────────────────────────────────────────────────
    session_id: Optional[str] = None
    provider_used: Optional[str] = None

    model_config = {"from_attributes": True}


class BatchSynthesisRequest(BaseModel):
    voice_id: str
    items: List[SynthesisRequest] = Field(..., min_length=1, max_length=100)


class BatchSynthesisResponse(BaseModel):
    batch_id: str
    total_items: int
    completed_items: int
    failed_items: int
    results: List[SynthesisResponse]


# ============ SSML Synthesis Schemas ============

class SSMLSynthesisRequest(BaseModel):
    """Synthesis request with SSML (Speech Synthesis Markup Language) support."""
    ssml: str = Field(
        ...,
        min_length=10,
        max_length=5000,
        description="SSML markup for fine-grained control over synthesis",
    )
    voice_id: str
    language: LanguageEnum  = LanguageEnum.EN
    stream: bool = Field(default=False)


class SSMLValidationResponse(BaseModel):
    """Response from SSML validation."""
    is_valid: bool
    error: Optional[str] = None
    plain_text: Optional[str] = None
    character_count: Optional[int] = None


# ============ Health & Status Schemas ============

class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    redis: str
    tts_model: str
    model_loaded: bool
    cache_enabled: bool
    timestamp: datetime


class MetricsResponse(BaseModel):
    total_syntheses: int
    total_characters: int
    avg_inference_time_ms: float
    cache_hit_rate: float
    active_jobs: int
    failed_jobs: int
    timestamp: datetime


# ============ Billing Schemas ============

class SubscriptionResponse(BaseModel):
    """Current subscription information."""
    tier: str  # free, starter, pro, enterprise
    status: str  # active, paused, canceled, inactive
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    monthly_character_limit: Optional[int] = None
    monthly_price: Optional[float] = None
    stripe_subscription_id: Optional[str] = None


class UpcomingInvoiceResponse(BaseModel):
    """Preview of next invoice."""
    amount_due: float
    currency: str
    period_start: datetime
    period_end: datetime
    due_date: Optional[datetime] = None
    lines: List[dict] = []


class InvoiceResponse(BaseModel):
    """Invoice information."""
    id: str
    stripe_invoice_id: str
    amount: float
    status: str  # draft, open, paid, uncollectible, void
    period_start: datetime
    period_end: datetime
    paid: bool
    paid_at: Optional[datetime] = None
    pdf_url: Optional[str] = None


class UsageResponse(BaseModel):
    """Current usage and billing information."""
    tier: str
    usage_characters: int
    remaining_quota: int
    usage_cost: float
    monthly_charge: float
    cost_per_million_chars: float
    period_end: Optional[datetime] = None


class SubscribeRequest(BaseModel):
    """Request to subscribe to a tier."""
    tier: str = Field(..., description="Tier: starter, pro, enterprise")


class CancelSubscriptionResponse(BaseModel):
    """Response when subscription is canceled."""
    status: str
    subscription_id: str
    at_period_end: bool
