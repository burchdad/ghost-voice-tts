"""
Analytics Dashboard for customer insights and usage tracking.

Provides detailed analytics on synthesis usage, voice quality metrics,
quota utilization, and marketplace performance.
"""

import logging
from typing import List, Optional, Tuple
from datetime import datetime, timedelta

from sqlmodel import Session, select, func
import numpy as np

from app.models.db import (
    User, Voice, SynthesisJob, VoiceContribution, FreeTrialGrant,
)

logger = logging.getLogger(__name__)


class AnalyticsDashboard:
    """Manage analytics and reporting for users."""
    
    def __init__(self, session: Session):
        self.session = session
    
    # ============ Usage Analytics ============
    
    def get_user_usage_stats(
        self,
        user_id: str,
        days: int = 30,
    ) -> dict:
        """Get user's synthesis usage stats for period."""
        start_date = datetime.utcnow() - timedelta(days=days)
        
        jobs = self.session.exec(
            select(SynthesisJob)
            .where(
                SynthesisJob.user_id == user_id,
                SynthesisJob.created_at >= start_date,
            )
        ).all()
        
        if not jobs:
            return {
                "period_days": days,
                "total_jobs": 0,
                "total_characters": 0,
                "avg_job_size": 0,
                "success_rate": 0,
                "daily_average": 0,
            }
        
        completed_jobs = [j for j in jobs if j.status == "completed"]
        
        return {
            "period_days": days,
            "total_jobs": len(jobs),
            "completed_jobs": len(completed_jobs),
            "failed_jobs": len([j for j in jobs if j.status == "failed"]),
            "total_characters": sum(len(j.text) for j in completed_jobs),
            "avg_job_size": (
                sum(len(j.text) for j in completed_jobs) / len(completed_jobs)
                if completed_jobs else 0
            ),
            "success_rate": len(completed_jobs) / len(jobs) if jobs else 0,
            "daily_average": len(jobs) / days if days > 0 else 0,
        }
    
    def get_daily_usage(
        self,
        user_id: str,
        days: int = 30,
    ) -> List[dict]:
        """Get day-by-day usage breakdown."""
        start_date = datetime.utcnow() - timedelta(days=days)
        
        daily_stats = {}
        for i in range(days):
            day = (start_date + timedelta(days=i)).date()
            daily_stats[day] = {
                "jobs": 0,
                "characters": 0,
                "success": 0,
                "failed": 0,
            }
        
        jobs = self.session.exec(
            select(SynthesisJob)
            .where(
                SynthesisJob.user_id == user_id,
                SynthesisJob.created_at >= start_date,
            )
        ).all()
        
        for job in jobs:
            day = job.created_at.date()
            if day not in daily_stats:
                daily_stats[day] = {
                    "jobs": 0,
                    "characters": 0,
                    "success": 0,
                    "failed": 0,
                }
            
            daily_stats[day]["jobs"] += 1
            daily_stats[day]["characters"] += len(job.text)
            
            if job.status == "completed":
                daily_stats[day]["success"] += 1
            else:
                daily_stats[day]["failed"] += 1
        
        return [
            {
                "date": str(date),
                **stats,
            }
            for date, stats in sorted(daily_stats.items())
        ]
    
    def get_voice_usage_stats(self, user_id: str) -> List[dict]:
        """Get usage stats per voice."""
        voices = self.session.exec(
            select(Voice).where(Voice.owner_id == user_id)
        ).all()
        
        stats = []
        for voice in voices:
            jobs = self.session.exec(
                select(SynthesisJob).where(SynthesisJob.voice_id == voice.id)
            ).all()
            
            completed_jobs = [j for j in jobs if j.status == "completed"]
            
            stats.append({
                "voice_id": voice.id,
                "voice_name": voice.name,
                "total_jobs": len(jobs),
                "completed": len(completed_jobs),
                "failed": len([j for j in jobs if j.status == "failed"]),
                "total_characters": sum(len(j.text) for j in completed_jobs),
                "avg_latency_ms": (
                    sum(j.inference_time_ms or 0 for j in completed_jobs) / len(completed_jobs)
                    if completed_jobs else 0
                ),
            })
        
        return sorted(stats, key=lambda x: x["total_jobs"], reverse=True)
    
    # ============ Performance Analytics ============
    
    def get_synthesis_performance(self, user_id: Optional[str] = None) -> dict:
        """Get synthesis performance metrics."""
        query = select(SynthesisJob).where(SynthesisJob.status == "completed")
        
        if user_id:
            query = query.where(SynthesisJob.user_id == user_id)
        
        jobs = self.session.exec(query.limit(1000)).all()
        
        if not jobs:
            return {
                "avg_latency_ms": 0,
                "p50_latency_ms": 0,
                "p95_latency_ms": 0,
                "p99_latency_ms": 0,
                "min_latency_ms": 0,
                "max_latency_ms": 0,
            }
        
        latencies = [j.inference_time_ms or 0 for j in jobs if j.inference_time_ms]
        
        if not latencies:
            return {
                "avg_latency_ms": 0,
                "p50_latency_ms": 0,
                "p95_latency_ms": 0,
                "p99_latency_ms": 0,
                "min_latency_ms": 0,
                "max_latency_ms": 0,
            }
        
        sorted_latencies = sorted(latencies)
        
        return {
            "sample_count": len(latencies),
            "avg_latency_ms": float(np.mean(latencies)),
            "p50_latency_ms": float(np.percentile(latencies, 50)),
            "p95_latency_ms": float(np.percentile(latencies, 95)),
            "p99_latency_ms": float(np.percentile(latencies, 99)),
            "min_latency_ms": float(np.min(latencies)),
            "max_latency_ms": float(np.max(latencies)),
        }
    
    def get_error_analytics(self, user_id: Optional[str] = None, days: int = 7) -> dict:
        """Get error rate analytics."""
        start_date = datetime.utcnow() - timedelta(days=days)
        
        query = select(SynthesisJob).where(SynthesisJob.created_at >= start_date)
        
        if user_id:
            query = query.where(SynthesisJob.user_id == user_id)
        
        jobs = self.session.exec(query).all()
        
        if not jobs:
            return {
                "period_days": days,
                "total_jobs": 0,
                "error_rate": 0,
                "errors_by_type": {},
            }
        
        errors_by_type = {}
        for job in jobs:
            if job.status == "failed":
                error_type = job.error_message.split(":")[0] if job.error_message else "unknown"
                errors_by_type[error_type] = errors_by_type.get(error_type, 0) + 1
        
        return {
            "period_days": days,
            "total_jobs": len(jobs),
            "successful": len([j for j in jobs if j.status == "completed"]),
            "failed": len([j for j in jobs if j.status == "failed"]),
            "error_rate": len([j for j in jobs if j.status == "failed"]) / len(jobs),
            "errors_by_type": errors_by_type,
        }
    
    # ============ Quota Analytics ============
    
    def get_quota_analytics(self, user_id: str) -> dict:
        """Get quota usage analytics."""
        user = self.session.exec(
            select(User).where(User.id == user_id)
        ).first()
        
        if not user:
            return {}
        
        # Current month usage
        start_of_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0)
        
        jobs = self.session.exec(
            select(SynthesisJob)
            .where(
                SynthesisJob.user_id == user_id,
                SynthesisJob.status == "completed",
                SynthesisJob.created_at >= start_of_month,
            )
        ).all()
        
        current_usage = sum(len(j.text) for j in jobs)
        
        # Projection for month
        days_elapsed = (datetime.utcnow() - start_of_month).days + 1
        projected_usage = (current_usage / days_elapsed * 30) if days_elapsed > 0 else 0
        
        return {
            "monthly_quota": user.monthly_synthesis_quota,
            "current_usage": current_usage,
            "remaining": user.monthly_synthesis_quota - current_usage,
            "usage_percentage": (current_usage / user.monthly_synthesis_quota * 100) if user.monthly_synthesis_quota > 0 else 0,
            "projected_usage": projected_usage,
            "will_exceed": projected_usage > user.monthly_synthesis_quota,
        }
    
    # ============ Voice Quality Analytics ============
    
    def get_voice_quality_scores(self) -> List[dict]:
        """Get average quality scores for all voices."""
        voices = self.session.exec(
            select(Voice).where(Voice.quality_score != None)
        ).all()
        
        quality_distribution = {
            "excellent": 0,  # >= 0.9
            "good": 0,       # 0.7-0.9
            "fair": 0,       # 0.5-0.7
            "poor": 0,       # < 0.5
        }
        
        for voice in voices:
            if voice.quality_score >= 0.9:
                quality_distribution["excellent"] += 1
            elif voice.quality_score >= 0.7:
                quality_distribution["good"] += 1
            elif voice.quality_score >= 0.5:
                quality_distribution["fair"] += 1
            else:
                quality_distribution["poor"] += 1
        
        return [
            {
                "category": category,
                "count": count,
                "percentage": (count / len(voices) * 100) if voices else 0,
            }
            for category, count in quality_distribution.items()
        ]
    
    # ============ Marketplace Analytics ============
    
    def get_marketplace_revenue_analytics(self, days: int = 30) -> dict:
        """Get marketplace revenue from voice contributions."""
        start_date = datetime.utcnow() - timedelta(days=days)
        
        # Get active trial grants from voice contributions
        trials = self.session.exec(
            select(FreeTrialGrant)
            .where(
                FreeTrialGrant.created_at >= start_date,
                FreeTrialGrant.is_active == True,
            )
        ).all()
        
        # Estimate savings (typical free trial value)
        estimated_savings = len(trials) * 100  # $100 per free trial
        
        # Get trial sign-ups who converted to paid
        converted = 0
        for trial in trials:
            user = self.session.exec(
                select(User).where(User.id == trial.user_id)
            ).first()
            
            if user and user.tier != "free":
                converted += 1
        
        return {
            "period_days": days,
            "free_trials_granted": len(trials),
            "converted_to_paid": converted,
            "conversion_rate": (converted / len(trials) * 100) if trials else 0,
            "estimated_revenue_prevented": estimated_savings,
        }
    
    def get_voice_marketplace_stats(self) -> dict:
        """Get overall voice marketplace statistics."""
        total_contributions = self.session.exec(
            select(func.count(VoiceContribution.id))
        ).one()
        
        active_contributions = self.session.exec(
            select(func.count(VoiceContribution.id))
            .where(VoiceContribution.status == "active")
        ).one()
        
        # Get most contributed languages
        langs = self.session.exec(
            select(Voice.language, func.count(Voice.id).label("count"))
            .select_from(Voice)
            .where(Voice.is_public == True)
            .group_by(Voice.language)
            .order_by(func.count(Voice.id).desc())
            .limit(10)
        ).all()
        
        return {
            "total_public_voices": self.session.exec(
                select(func.count(Voice.id)).where(Voice.is_public == True)
            ).one(),
            "total_contributions": total_contributions,
            "active_contributions": active_contributions,
            "top_languages": [
                {"language": lang, "count": count}
                for lang, count in langs
            ],
        }
    
    # ============ Trends ============
    
    def get_usage_trends(self, days: int = 30) -> dict:
        """Get usage trend indicators."""
        start_date = datetime.utcnow() - timedelta(days=days)
        mid_date = datetime.utcnow() - timedelta(days=days // 2)
        
        # First half
        first_half_jobs = self.session.exec(
            select(SynthesisJob)
            .where(
                SynthesisJob.status == "completed",
                SynthesisJob.created_at >= start_date,
                SynthesisJob.created_at < mid_date,
            )
        ).all()
        
        # Second half
        second_half_jobs = self.session.exec(
            select(SynthesisJob)
            .where(
                SynthesisJob.status == "completed",
                SynthesisJob.created_at >= mid_date,
            )
        ).all()
        
        first_half_chars = sum(len(j.text) for j in first_half_jobs)
        second_half_chars = sum(len(j.text) for j in second_half_jobs)
        
        growth = (
            ((second_half_chars - first_half_chars) / first_half_chars * 100)
            if first_half_chars > 0 else 0
        )
        
        return {
            "period_days": days,
            "first_half_jobs": len(first_half_jobs),
            "second_half_jobs": len(second_half_jobs),
            "first_half_characters": first_half_chars,
            "second_half_characters": second_half_chars,
            "job_growth_percent": (
                ((len(second_half_jobs) - len(first_half_jobs)) / len(first_half_jobs) * 100)
                if first_half_jobs else 0
            ),
            "character_growth_percent": growth,
            "trend": "up" if growth > 0 else "down" if growth < 0 else "flat",
        }


def get_analytics_dashboard(session: Session) -> AnalyticsDashboard:
    """Get analytics dashboard instance."""
    return AnalyticsDashboard(session)
