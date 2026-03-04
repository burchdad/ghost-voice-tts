"""
Admin Dashboard Routes for operational management.

Protected endpoints for system operators and admins.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.dependencies import get_session, verify_admin
from app.services.admin_dashboard import get_admin_dashboard

router = APIRouter(prefix="/admin", tags=["admin"])


# ============ User Management ============

@router.get("/users")
async def get_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    tier: str = Query(None),
    search: str = Query(None),
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Get users with filtering and pagination."""
    dashboard = get_admin_dashboard(session)
    users = dashboard.get_users(skip=skip, limit=limit, tier=tier, search=search)
    
    return {
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "username": u.username,
                "tier": u.tier,
                "is_active": u.is_active,
                "created_at": u.created_at,
            }
            for u in users
        ],
        "count": len(users),
    }


@router.get("/users/{user_id}/metrics")
async def get_user_metrics(
    user_id: str,
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Get metrics for specific user."""
    dashboard = get_admin_dashboard(session)
    metrics = dashboard.get_user_metrics(user_id)
    
    if not metrics:
        raise HTTPException(status_code=404, detail="User not found")
    
    return metrics


@router.post("/users/{user_id}/quota")
async def adjust_user_quota(
    user_id: str,
    adjustment: int,
    reason: str = "",
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Adjust user monthly quota."""
    dashboard = get_admin_dashboard(session)
    success = dashboard.adjust_user_quota(user_id, adjustment, reason)
    
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"success": True, "adjustment": adjustment}


@router.post("/users/{user_id}/tier")
async def upgrade_user_tier(
    user_id: str,
    new_tier: str,
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Upgrade user to higher tier."""
    if new_tier not in ["free", "starter", "pro", "enterprise"]:
        raise HTTPException(status_code=400, detail="Invalid tier")
    
    dashboard = get_admin_dashboard(session)
    success = dashboard.upgrade_user_tier(user_id, new_tier)
    
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"success": True, "new_tier": new_tier}


@router.post("/users/{user_id}/suspend")
async def suspend_user(
    user_id: str,
    reason: str = "",
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Suspend user account."""
    dashboard = get_admin_dashboard(session)
    success = dashboard.suspend_user(user_id, reason)
    
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"success": True, "suspended": True}


# ============ Voice Moderation ============

@router.get("/voices/pending")
async def get_pending_voices(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Get voices pending verification."""
    dashboard = get_admin_dashboard(session)
    voices = dashboard.get_pending_voices(skip=skip, limit=limit)
    
    return {
        "voices": [
            {
                "id": v.id,
                "name": v.name,
                "owner_id": v.owner_id,
                "gender": v.gender,
                "accent": v.accent,
                "language": v.language,
                "quality_score": v.quality_score,
                "created_at": v.created_at,
            }
            for v in voices
        ],
        "count": len(voices),
    }


@router.post("/voices/{voice_id}/verify")
async def verify_voice(
    voice_id: str,
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Mark voice as verified."""
    dashboard = get_admin_dashboard(session)
    success = dashboard.verify_voice(voice_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    return {"success": True, "verified": True}


@router.post("/voices/{voice_id}/reject")
async def reject_voice(
    voice_id: str,
    reason: str = "",
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Reject voice from public listing."""
    dashboard = get_admin_dashboard(session)
    success = dashboard.reject_voice(voice_id, reason)
    
    if not success:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    return {"success": True, "rejected": True}


# ============ System Monitoring ============

@router.get("/health")
async def get_system_health(
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Get overall system health metrics."""
    dashboard = get_admin_dashboard(session)
    health = dashboard.get_system_health()
    
    return health


@router.get("/top-users")
async def get_top_users(
    metric: str = Query("synthesis_count", regex="^(synthesis_count|characters)$"),
    limit: int = Query(10, ge=1, le=100),
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Get top users by metric."""
    dashboard = get_admin_dashboard(session)
    top_users = dashboard.get_top_users(metric=metric, limit=limit)
    
    return {"metric": metric, "users": top_users}


# ============ Marketplace Insights ============

@router.get("/marketplace/insights")
async def get_marketplace_insights(
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Get insights on voice marketplace."""
    dashboard = get_admin_dashboard(session)
    insights = dashboard.get_marketplace_insights()
    
    return insights


# ============ Reports ============

@router.get("/reports/daily")
async def get_daily_report(
    session: Session = Depends(get_session),
    admin: dict = Depends(verify_admin),
):
    """Generate daily usage report."""
    dashboard = get_admin_dashboard(session)
    report = dashboard.generate_daily_report()
    
    return report
