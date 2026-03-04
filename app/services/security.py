"""
Security and authentication service.

Implements:
- JWT token generation and validation
- API key authentication
- Request signing/verification
- Session management
"""

import hmac
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from app.core.config import settings


# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenData(BaseModel):
    """JWT token payload."""
    user_id: str
    email: Optional[str] = None
    tier: str = "free"
    exp: int


class TokenResponse(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class APIKey(BaseModel):
    """API key info."""
    key: str  # hashed
    label: str
    created_at: datetime
    last_used: Optional[datetime] = None
    active: bool = True


class SecurityManager:
    """
    Manages authentication and security operations.
    
    Supports:
    - JWT token generation and validation
    - API key authentication (hashed)
    - Request signature verification
    - Password hashing
    """
    
    def __init__(self):
        self.algorithm = "HS256"
        self.secret_key = settings.SECRET_KEY
    
    # ============ JWT Token Management ============
    
    def create_access_token(
        self,
        user_id: str,
        email: Optional[str] = None,
        tier: str = "free",
        expires_delta: Optional[timedelta] = None,
    ) -> TokenResponse:
        """
        Create JWT access token.
        
        Args:
            user_id: User ID
            email: User email (optional)
            tier: User subscription tier
            expires_delta: Token expiration time (default 24h)
        
        Returns:
            TokenResponse with access_token
        """
        if expires_delta is None:
            expires_delta = timedelta(hours=24)
        
        expire = datetime.utcnow() + expires_delta
        
        to_encode: Dict[str, Any] = {
            "user_id": user_id,
            "email": email,
            "tier": tier,
            "exp": int(expire.timestamp()),
        }
        
        encoded_jwt = jwt.encode(
            to_encode,
            self.secret_key,
            algorithm=self.algorithm,
        )
        
        return TokenResponse(
            access_token=encoded_jwt,
            expires_in=int(expires_delta.total_seconds()),
        )
    
    def verify_token(self, token: str) -> Optional[TokenData]:
        """
        Verify and decode JWT token.
        
        Args:
            token: JWT token string
        
        Returns:
            TokenData if valid, None if invalid/expired
        """
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            user_id: str = payload.get("user_id")
            
            if user_id is None:
                return None
            
            return TokenData(**payload)
        except JWTError:
            return None
    
    def create_refresh_token(
        self,
        user_id: str,
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """Create refresh token (longer-lived)."""
        if expires_delta is None:
            expires_delta = timedelta(days=30)
        
        expire = datetime.utcnow() + expires_delta
        
        to_encode = {
            "type": "refresh",
            "user_id": user_id,
            "exp": int(expire.timestamp()),
        }
        
        return jwt.encode(
            to_encode,
            self.secret_key,
            algorithm=self.algorithm,
        )
    
    # ============ API Key Management ============
    
    def generate_api_key(self, label: str = "") -> tuple[str, str]:
        """
        Generate new API key.
        
        Returns:
            (raw_key, hashed_key)
            - raw_key: Give to user
            - hashed_key: Store in database
        """
        raw_key = f"sk_{secrets.token_urlsafe(32)}"
        hashed_key = hashlib.sha256(raw_key.encode()).hexdigest()
        return raw_key, hashed_key
    
    def hash_api_key(self, key: str) -> str:
        """Hash API key for storage."""
        return hashlib.sha256(key.encode()).hexdigest()
    
    def verify_api_key(self, raw_key: str, hashed_key: str) -> bool:
        """Verify API key matches hash."""
        return hmac.compare_digest(self.hash_api_key(raw_key), hashed_key)
    
    # ============ Request Signing ============
    
    def sign_request(self, payload: str, timestamp: str) -> str:
        """
        Sign request payload for verification.
        
        Args:
            payload: Request body as string
            timestamp: Request timestamp
        
        Returns:
            Signature (hex string)
        """
        message = f"{timestamp}.{payload}"
        signature = hmac.new(
            self.secret_key.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return signature
    
    def verify_request_signature(
        self,
        payload: str,
        timestamp: str,
        signature: str,
        tolerance_seconds: int = 300,
    ) -> bool:
        """
        Verify request signature and timestamp.
        
        Args:
            payload: Request body as string
            timestamp: Request timestamp from header
            signature: Signature from header
            tolerance_seconds: Max age of timestamp (5 min default)
        
        Returns:
            True if signature is valid and timestamp is fresh
        """
        # Check timestamp freshness
        try:
            ts = float(timestamp)
            if abs(datetime.utcnow().timestamp() - ts) > tolerance_seconds:
                return False
        except (ValueError, TypeError):
            return False
        
        # Verify signature
        expected_signature = self.sign_request(payload, timestamp)
        return hmac.compare_digest(signature, expected_signature)
    
    # ============ Password Hashing ============
    
    def hash_password(self, password: str) -> str:
        """Hash password for storage."""
        return pwd_context.hash(password)
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify password against hash."""
        return pwd_context.verify(plain_password, hashed_password)
    
    # ============ Session Management ============
    
    def create_session_token(self, user_id: str) -> str:
        """Create session token (for web UI)."""
        expires_delta = timedelta(hours=8)
        expire = datetime.utcnow() + expires_delta
        
        to_encode = {
            "type": "session",
            "user_id": user_id,
            "exp": int(expire.timestamp()),
            "jti": secrets.token_urlsafe(16),  # Unique ID for revocation
        }
        
        return jwt.encode(
            to_encode,
            self.secret_key,
            algorithm=self.algorithm,
        )
    
    def verify_session_token(self, token: str) -> Optional[str]:
        """Verify session token and return user_id."""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            
            if payload.get("type") != "session":
                return None
            
            return payload.get("user_id")
        except JWTError:
            return None


# Singleton instance
_security_manager: Optional[SecurityManager] = None


def get_security_manager() -> SecurityManager:
    """Get or create security manager instance."""
    global _security_manager
    if _security_manager is None:
        _security_manager = SecurityManager()
    return _security_manager
