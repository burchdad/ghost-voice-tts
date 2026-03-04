from pydantic import BaseModel, Field, EmailStr, HttpUrl
from typing import Optional, List
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


class SynthesisResponse(BaseModel):
    id: str
    status: StatusEnum
    audio_url: Optional[str] = None
    audio_duration: Optional[float] = None
    progress: float
    created_at: datetime
    completed_at: Optional[datetime] = None
    inference_time_ms: Optional[float] = None
    
    model_config = {"from_attributes": True}


class BatchSynthesisRequest(BaseModel):
    voice_id: str
    items: List[SynthesisRequest] = Field(..., min_items=1, max_items=100)


class BatchSynthesisResponse(BaseModel):
    batch_id: str
    total_items: int
    completed_items: int
    failed_items: int
    results: List[SynthesisResponse]


# ============ Health & Status Schemas ============

class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    redis: str
    tts_model: str
    timestamp: datetime


class MetricsResponse(BaseModel):
    total_syntheses: int
    total_characters: int
    avg_inference_time_ms: float
    cache_hit_rate: float
    active_jobs: int
    failed_jobs: int
    timestamp: datetime
