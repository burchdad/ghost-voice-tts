"""
Voice marketplace service.

Manages voice contributions, consent, free trial grants, and marketplace logic.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from sqlmodel import Session, select

from app.models.db import User, Voice, VoiceContribution, FreeTrialGrant
from app.core.metrics import MetricsCollector


logger = logging.getLogger(__name__)


class VoiceMarketplaceManager:
    """
    Manages the voice contribution marketplace.
    
    Business logic:
    - Users can opt-in to contribute voices for training data
    - Contributors get 2 months free access as reward
    - Each contributed voice can be used in the model training pipeline
    - Revenue sharing / quality metrics track voice value
    """
    
    # Configuration
    FREE_TRIAL_DURATION_DAYS = 60  # 2 months
    INITIAL_VOICE_DONATION_QUOTA = 1_000_000  # Extra chars
    
    def __init__(self, session: Session):
        self.session = session
    
    async def grant_voice_contribution_reward(
        self,
        user: User,
        voice: Voice,
        consent_version: str = "1.0",
    ) -> FreeTrialGrant:
        """
        Grant free trial to user who contributes a voice.
        
        Args:
            user: User contributing the voice
            voice: Voice being contributed
            consent_version: Version of consent terms accepted
        
        Returns:
            FreeTrialGrant object
        """
        logger.info(f"Granting free trial for voice contribution: user={user.id}, voice={voice.id}")
        
        # Create voice contribution record
        contribution = VoiceContribution(
            user_id=user.id,
            voice_id=voice.id,
            voice_name=voice.name,
            description=voice.description,
            consent_granted=True,
            consent_version=consent_version,
        )
        self.session.add(contribution)
        
        # Create free trial grant
        start_date = datetime.utcnow()
        end_date = start_date + timedelta(days=self.FREE_TRIAL_DURATION_DAYS)
        
        grant = FreeTrialGrant(
            user_id=user.id,
            start_date=start_date,
            end_date=end_date,
            grant_reason="voice_contribution",
            related_voice_id=voice.id,
            bonus_monthly_quota=self.INITIAL_VOICE_DONATION_QUOTA,
        )
        self.session.add(grant)
        
        # Update user stats
        user.voices_contributed += 1
        user.has_contributed_voice = True
        user.voice_consent_granted = True
        user.voice_consent_updated_at = datetime.utcnow()
        user.current_free_period_end = end_date
        user.free_periods_used += 1
        user.tier = "starter"  # Upgrade to starter tier
        
        self.session.add(user)
        self.session.commit()
        
        MetricsCollector.record_metric("voice_contributions", 1)
        
        return grant
    
    async def get_active_free_period(self, user: User) -> Optional[FreeTrialGrant]:
        """Get user's active free trial grant."""
        now = datetime.utcnow()
        
        statement = select(FreeTrialGrant).where(
            FreeTrialGrant.user_id == user.id,
            FreeTrialGrant.start_date <= now,
            FreeTrialGrant.end_date > now,
            FreeTrialGrant.is_active == True,
        )
        
        grant = self.session.exec(statement).first()
        return grant
    
    async def check_and_apply_free_period_quota(
        self,
        user: User,
        requested_chars: int,
    ) -> Tuple[bool, int]:  # (has_free_period, bonus_quota_remaining)
        """
        Check if user has active free period and return bonus quota.
        
        Returns:
            (is_in_free_period, bonus_chars_remaining)
        """
        grant = await self.get_active_free_period(user)
        
        if not grant:
            return False, 0
        
        # Calculate current usage in this period
        start_of_period = grant.start_date
        
        # Query synthesis jobs created during this period
        from app.models.db import SynthesisJob
        
        statement = select(SynthesisJob).where(
            SynthesisJob.user_id == user.id,
            SynthesisJob.created_at >= start_of_period,
            SynthesisJob.status == "completed",
        )
        
        jobs = self.session.exec(statement).all()
        chars_used_in_period = sum(len(job.text) for job in jobs)
        
        bonus_remaining = max(0, grant.bonus_monthly_quota - chars_used_in_period)
        
        return True, bonus_remaining
    
    async def get_user_voice_contributions(
        self,
        user: User,
        active_only: bool = True,
    ) -> List[VoiceContribution]:
        """Get voices contributed by user."""
        statement = select(VoiceContribution).where(
            VoiceContribution.user_id == user.id,
        )
        
        if active_only:
            statement = statement.where(VoiceContribution.status == "active")
        
        contributions = self.session.exec(statement).all()
        return contributions
    
    async def withdraw_voice_contribution(
        self,
        contribution_id: str,
        reason: Optional[str] = None,
    ) -> VoiceContribution:
        """
        Withdraw a voice contribution from training pool.
        
        Note: Users can withdraw but may lose free period benefits if not yet used.
        """
        contribution = self.session.get(VoiceContribution, contribution_id)
        
        if not contribution:
            raise ValueError(f"Contribution not found: {contribution_id}")
        
        contribution.status = "withdrawn"
        contribution.rejection_reason = reason or "User requested withdrawal"
        contribution.consent_granted = False
        
        self.session.add(contribution)
        self.session.commit()
        
        logger.info(f"Voice contribution withdrawn: {contribution_id}")
        MetricsCollector.record_metric("voice_contributions_withdrawn", 1)
        
        return contribution
    
    async def get_marketplace_stats(self) -> dict:
        """Get statistics about marketplace."""
        # Total contributors
        statement = select(User).where(User.has_contributed_voice == True)
        total_contributors = len(self.session.exec(statement).all())
        
        # Total active contributions
        statement = select(VoiceContribution).where(
            VoiceContribution.status == "active",
            VoiceContribution.consent_granted == True,
        )
        total_active_voices = len(self.session.exec(statement).all())
        
        # Active free period grants
        now = datetime.utcnow()
        statement = select(FreeTrialGrant).where(
            FreeTrialGrant.start_date <= now,
            FreeTrialGrant.end_date > now,
            FreeTrialGrant.is_active == True,
        )
        active_free_periods = len(self.session.exec(statement).all())
        
        return {
            "total_voice_contributors": total_contributors,
            "total_active_contributed_voices": total_active_voices,
            "active_free_trial_periods": active_free_periods,
        }
    
    async def get_voice_usage_stats(self, voice_id: str) -> dict:
        """Get usage statistics for a specific voice."""
        from app.models.db import SynthesisJob
        
        # Count times voice has been used
        statement = select(SynthesisJob).where(
            SynthesisJob.voice_id == voice_id,
            SynthesisJob.status == "completed",
        )
        jobs = self.session.exec(statement).all()
        
        total_uses = len(jobs)
        total_chars = sum(len(job.text) for job in jobs)
        
        # Get contribution info
        statement = select(VoiceContribution).where(
            VoiceContribution.voice_id == voice_id,
        )
        contributions = self.session.exec(statement).all()
        
        contribution = contributions[0] if contributions else None
        
        return {
            "voice_id": voice_id,
            "total_uses": total_uses,
            "total_characters": total_chars,
            "is_contributed_voice": contribution is not None,
            "contribution_status": contribution.status if contribution else None,
        }


# Singleton instance
_marketplace_manager: Optional[VoiceMarketplaceManager] = None


def get_marketplace_manager(session: Session) -> VoiceMarketplaceManager:
    """Get marketplace manager instance."""
    return VoiceMarketplaceManager(session)
