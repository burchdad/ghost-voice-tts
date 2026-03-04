"""
Admin Dashboard API for operational management.

Endpoints for user management, system monitoring, voice moderation, etc.
"""

import logging
from typing import List, Optional
from datetime import datetime, timedelta

from sqlmodel import Session, select, func

from app.models.db import User, Voice, SynthesisJob, VoiceContribution, FreeTrialGrant

logger = logging.getLogger(__name__)


class AdminDashboardManager:
    """Manage admin dashboard operations."""
    
    def __init__(self, session: Session):
        self.session = session
    
    # ============ User Management ============
    
    def get_users(
        self,
        skip: int = 0,
        limit: int = 100,
        tier: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[User]:
        """Get users with filtering and pagination."""
        query = select(User)
        
        if tier:
            query = query.where(User.tier == tier)
        
        if search:
            query = query.where(
                (User.email.contains(search)) | (User.username.contains(search))
            )
        
        return self.session.exec(query.offset(skip).limit(limit)).all()
    
    def get_user_metrics(self, user_id: str) -> dict:
        """Get metrics for specific user."""
        user = self.session.exec(
            select(User).where(User.id == user_id)
        ).first()
        
        if not user:
            return {}
        
        # Synthesis stats
        synthesis_jobs = self.session.exec(
            select(SynthesisJob).where(SynthesisJob.user_id == user_id)
        ).all()
        
        completed_jobs = [j for j in synthesis_jobs if j.status == "completed"]
        failed_jobs = [j for j in synthesis_jobs if j.status == "failed"]
        
        total_characters = sum(len(j.text) for j in completed_jobs)
        avg_inference_time = (
            sum(j.inference_time_ms or 0 for j in completed_jobs) / len(completed_jobs)
            if completed_jobs else 0
        )
        
        # Voice stats
        voices = self.session.exec(
            select(Voice).where(Voice.owner_id == user_id)
        ).all()
        
        return {
            "user_id": user_id,
            "email": user.email,
            "username": user.username,
            "tier": user.tier,
            "synthesis": {
                "total_jobs": len(synthesis_jobs),
                "completed": len(completed_jobs),
                "failed": len(failed_jobs),
                "total_characters": total_characters,
                "avg_inference_time_ms": avg_inference_time,
            },
            "quota": {
                "monthly_quota": user.monthly_synthesis_quota,
                "current_usage": user.current_month_usage,
                "remaining": user.monthly_synthesis_quota - user.current_month_usage,
            },
            "voices": len(voices),
            "created_at": user.created_at,
        }
    
    def adjust_user_quota(
        self,
        user_id: str,
        adjustment: int,
        reason: str = "",
    ) -> bool:
        """Adjust user monthly quota (admin action)."""
        user = self.session.exec(
            select(User).where(User.id == user_id)
        ).first()
        
        if not user:
            return False
        
        user.monthly_synthesis_quota += adjustment
        self.session.add(user)
        self.session.commit()
        
        logger.info(
            f"Adjusted quota for {user.email}: +{adjustment} "
            f"(new: {user.monthly_synthesis_quota}) - {reason}"
        )
        return True
    
    def upgrade_user_tier(self, user_id: str, new_tier: str) -> bool:
        """Upgrade user to higher tier."""
        user = self.session.exec(
            select(User).where(User.id == user_id)
        ).first()
        
        if not user:
            return False
        
        old_tier = user.tier
        user.tier = new_tier
        user.is_premium = new_tier != "free"
        
        # Increase quota based on tier
        tier_quotas = {
            "free": 100_000,
            "starter": 1_000_000,
            "pro": 10_000_000,
            "enterprise": 100_000_000,
        }
        user.monthly_synthesis_quota = tier_quotas.get(new_tier, 100_000)
        
        self.session.add(user)
        self.session.commit()
        
        logger.info(f"Upgraded {user.email} from {old_tier} to {new_tier}")
        return True
    
    def suspend_user(self, user_id: str, reason: str = "") -> bool:
        """Suspend user account."""
        user = self.session.exec(
            select(User).where(User.id == user_id)
        ).first()
        
        if not user:
            return False
        
        user.is_active = False
        self.session.add(user)
        self.session.commit()
        
        logger.warning(f"Suspended user {user.email} - {reason}")
        return True
    
    # ============ Voice Moderation ============
    
    def get_pending_voices(self, skip: int = 0, limit: int = 50) -> List[Voice]:
        """Get voices pending verification."""
        return self.session.exec(
            select(Voice)
            .where(Voice.is_verified == False, Voice.is_public == True)
            .offset(skip)
            .limit(limit)
        ).all()
    
    def verify_voice(self, voice_id: str) -> bool:
        """Mark voice as verified."""
        voice = self.session.exec(
            select(Voice).where(Voice.id == voice_id)
        ).first()
        
        if not voice:
            return False
        
        voice.is_verified = True
        self.session.add(voice)
        self.session.commit()
        
        logger.info(f"Verified voice {voice_id}: {voice.name}")
        return True
    
    def reject_voice(self, voice_id: str, reason: str = "") -> bool:
        """Reject voice from public listing."""
        voice = self.session.exec(
            select(Voice).where(Voice.id == voice_id)
        ).first()
        
        if not voice:
            return False
        
        voice.is_public = False
        voice.is_verified = False
        self.session.add(voice)
        self.session.commit()
        
        logger.warning(f"Rejected voice {voice_id}: {reason}")
        return True
    
    # ============ System Monitoring ============
    
    def get_system_health(self) -> dict:
        """Get overall system health metrics."""
        now = datetime.utcnow()
        last_hour = now - timedelta(hours=1)
        
        # User growth
        total_users = self.session.exec(select(func.count(User.id))).one()
        new_users_1h = self.session.exec(
            select(func.count(User.id)).where(User.created_at >= last_hour)
        ).one()
        
        # Synthesis activity
        total_jobs = self.session.exec(select(func.count(SynthesisJob.id))).one()
        completed_jobs = self.session.exec(
            select(func.count(SynthesisJob.id)).where(SynthesisJob.status == "completed")
        ).one()
        failed_jobs = self.session.exec(
            select(func.count(SynthesisJob.id)).where(SynthesisJob.status == "failed")
        ).one()
        
        # Performance
        recent_jobs = self.session.exec(
            select(SynthesisJob)
            .where(SynthesisJob.inference_time_ms != None)
            .order_by(SynthesisJob.created_at.desc())
            .limit(100)
        ).all()
        
        if recent_jobs:
            avg_latency = sum(j.inference_time_ms or 0 for j in recent_jobs) / len(recent_jobs)
            p95_latency = sorted([j.inference_time_ms or 0 for j in recent_jobs])[int(len(recent_jobs) * 0.95)]
        else:
            avg_latency = p95_latency = 0
        
        return {
            "users": {
                "total": total_users,
                "new_1h": new_users_1h,
            },
            "synthesis": {
                "total_jobs": total_jobs,
                "completed": completed_jobs,
                "failed": failed_jobs,
                "success_rate": completed_jobs / total_jobs if total_jobs > 0 else 0,
            },
            "performance": {
                "avg_latency_ms": avg_latency,
                "p95_latency_ms": p95_latency,
            },
            "timestamp": now,
        }
    
    def get_top_users(self, metric: str = "synthesis_count", limit: int = 10) -> List[dict]:
        """Get top users by metric."""
        if metric == "synthesis_count":
            users = self.session.exec(
                select(User.id, User.email, func.count(SynthesisJob.id).label("count"))
                .select_from(User)
                .outerjoin(SynthesisJob)
                .group_by(User.id)
                .order_by(func.count(SynthesisJob.id).desc())
                .limit(limit)
            ).all()
            
            return [
                {"user_id": u[0], "email": u[1], "synthesis_jobs": u[2]}
                for u in users
            ]
        
        elif metric == "characters":
            users = self.session.exec(
                select(User.id, User.email, func.sum(func.length(SynthesisJob.text)).label("chars"))
                .select_from(User)
                .outerjoin(SynthesisJob)
                .where(SynthesisJob.status == "completed")
                .group_by(User.id)
                .order_by(func.sum(func.length(SynthesisJob.text)).desc())
                .limit(limit)
            ).all()
            
            return [
                {"user_id": u[0], "email": u[1], "total_characters": u[2] or 0}
                for u in users
            ]
        
        return []
    
    # ============ Marketplace Insights ============
    
    def get_marketplace_insights(self) -> dict:
        """Get insights on voice marketplace."""
        total_contributions = self.session.exec(
            select(func.count(VoiceContribution.id))
        ).one()
        
        active_contributions = self.session.exec(
            select(func.count(VoiceContribution.id)).where(
                VoiceContribution.status == "active"
            )
        ).one()
        
        active_trials = self.session.exec(
            select(func.count(FreeTrialGrant.id)).where(
                FreeTrialGrant.is_active == True,
                FreeTrialGrant.end_date > datetime.utcnow(),
            )
        ).one()
        
        total_conversions = self.session.exec(
            select(func.count(User.id)).where(User.tier != "free")
        ).one()
        
        return {
            "voice_contributions": {
                "total": total_contributions,
                "active": active_contributions,
                "conversion_rate": active_contributions / total_contributions if total_contributions > 0 else 0,
            },
            "free_trials": {
                "active": active_trials,
            },
            "customer_conversion": {
                "paying_customers": total_conversions,
                "conversion_rate": total_conversions / self.session.exec(select(func.count(User.id))).one() if self.session.exec(select(func.count(User.id))).one() > 0 else 0,
            },
        }
    
    # ============ Generate Reports ============
    
    def generate_daily_report(self) -> dict:
        """Generate daily usage report."""
        yesterday = datetime.utcnow() - timedelta(days=1)
        
        jobs = self.session.exec(
            select(SynthesisJob).where(SynthesisJob.created_at >= yesterday)
        ).all()
        
        return {
            "date": yesterday.date(),
            "synthesis_jobs": len(jobs),
            "completed": len([j for j in jobs if j.status == "completed"]),
            "failed": len([j for j in jobs if j.status == "failed"]),
            "total_characters": sum(len(j.text) for j in jobs),
            "avg_inference_time_ms": (
                sum(j.inference_time_ms or 0 for j in [j for j in jobs if j.status == "completed"]) /
                len([j for j in jobs if j.status == "completed"])
                if [j for j in jobs if j.status == "completed"] else 0
            ),
        }


def get_admin_dashboard(session: Session) -> AdminDashboardManager:
    """Get admin dashboard manager."""
    return AdminDashboardManager(session)
