import logging
from datetime import datetime, timedelta
from typing import Tuple
from sqlmodel import Session

from app.core.config import get_settings
from app.models.db import User

logger = logging.getLogger(__name__)
settings = get_settings()


class QuotaExceeded(Exception):
    """Raised when user exceeds quota."""
    pass


class QuotaManager:
    """Manage user synthesis quotas and rate limiting."""
    
    def __init__(self):
        self.rate_limit_requests = settings.RATE_LIMIT_REQUESTS
        self.rate_limit_period = settings.RATE_LIMIT_PERIOD  # seconds
    
    def check_monthly_quota(
        self,
        user: User,
        text_length: int,
    ) -> Tuple[bool, int]:
        """
        Check if user can synthesize this text.
        
        Returns:
            (can_proceed, remaining_quota)
        """
        
        remaining = user.monthly_synthesis_quota - user.current_month_usage
        
        if text_length > remaining:
            logger.warning(
                f"Quota exceeded for user {user.id}: "
                f"requested {text_length}, remaining {remaining}"
            )
            return False, remaining
        
        return True, remaining - text_length
    
    def deduct_quota(
        self,
        user: User,
        text_length: int,
        session: Session,
    ) -> None:
        """Deduct characters from user's monthly quota."""
        
        user.current_month_usage += text_length
        session.add(user)
        session.commit()
        
        logger.info(
            f"Deducted {text_length} chars from user {user.id}. "
            f"Usage: {user.current_month_usage}/{user.monthly_synthesis_quota}"
        )
    
    def reset_monthly_quota(
        self,
        user: User,
        session: Session,
    ) -> None:
        """Reset user's monthly quota (call on month boundary)."""
        
        user.current_month_usage = 0
        session.add(user)
        session.commit()
        
        logger.info(f"Reset monthly quota for user {user.id}")
    
    def get_usage_percentage(self, user: User) -> float:
        """Get percentage of quota used."""
        return (user.current_month_usage / user.monthly_synthesis_quota) * 100
    
    def get_quota_info(self, user: User) -> dict:
        """Get quota information for user."""
        
        return {
            "monthly_quota": user.monthly_synthesis_quota,
            "used": user.current_month_usage,
            "remaining": user.monthly_synthesis_quota - user.current_month_usage,
            "usage_percent": self.get_usage_percentage(user),
            "reset_on": self._get_next_month_first_day(),
        }
    
    @staticmethod
    def _get_next_month_first_day() -> datetime:
        """Get first day of next month."""
        today = datetime.utcnow()
        first_of_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return first_of_next


# Singleton instance
_quota_manager = None


def get_quota_manager() -> QuotaManager:
    """Get or create quota manager."""
    global _quota_manager
    if _quota_manager is None:
        _quota_manager = QuotaManager()
    return _quota_manager
