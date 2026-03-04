"""
Authentication dependencies for FastAPI endpoints.

Provides dependency functions for route protection.
"""

from typing import Optional
from fastapi import Depends, HTTPException, status, Header

from app.services.security import get_security_manager
from app.models.db import User
from app.core.database import get_session
from sqlmodel import Session


async def get_current_user(
    authorization: Optional[str] = Header(None),
    session: Session = Depends(get_session),
) -> User:
    """
    Verify JWT token and return current user.
    
    Can be used as dependency: @app.get("/me", dependencies=[Depends(get_current_user)])
    
    Or as parameter decorator: async def endpoint(user: User = Depends(get_current_user))
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = authorization.split(" ")[1]
    security_mgr = get_security_manager()
    token_data = security_mgr.verify_token(token)
    
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get user from database
    user = session.query(User).filter(User.id == token_data.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    return user


async def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    session: Session = Depends(get_session),
) -> Optional[User]:
    """
    Optionally verify JWT token, returns None if not provided.
    Useful for endpoints that work with or without authentication.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    
    token = authorization.split(" ")[1]
    security_mgr = get_security_manager()
    token_data = security_mgr.verify_token(token)
    
    if not token_data:
        return None
    
    user = session.query(User).filter(User.id == token_data.user_id).first()
    return user


async def get_current_user_from_api_key(
    x_api_key: Optional[str] = Header(None),
    session: Session = Depends(get_session),
) -> User:
    """
    Verify API key and return current user.
    Useful for programmatic/server-to-server access.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    
    # Query database for API key
    from app.models.db import APIKeyModel
    
    api_key_model = session.query(APIKeyModel).filter(
        APIKeyModel.hashed_key == x_api_key,
        APIKeyModel.active == True,
    ).first()
    
    if not api_key_model:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    
    # Get user
    user = session.query(User).filter(User.id == api_key_model.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    # Update last_used
    import datetime
    api_key_model.last_used = datetime.datetime.utcnow()
    session.add(api_key_model)
    session.commit()
    
    return user


async def get_admin_user(
    user: User = Depends(get_current_user),
) -> User:
    """
    Verify user is admin.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


# Alias for admin verification (used in admin and analytics routes)
verify_admin = get_admin_user
