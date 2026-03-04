"""
Analytics Routes for user insights and usage tracking.

Endpoints for viewing synthesis statistics, quota usage, performance metrics, etc.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.dependencies import get_session, get_current_user
from app.services.analytics import get_analytics_dashboard

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ============ Usage Analytics ============

@router.get("/usage/summary")
async def get_usage_summary(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get synthesis usage summary."""
    dashboard = get_analytics_dashboard(session)
    stats = dashboard.get_user_usage_stats(user["id"], days=days)
    
    return stats


@router.get("/usage/daily")
async def get_daily_usage(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get day-by-day usage breakdown."""
    dashboard = get_analytics_dashboard(session)
    daily = dashboard.get_daily_usage(user["id"], days=days)
    
    return {"daily_stats": daily}


@router.get("/usage/by-voice")
async def get_voice_usage_stats(
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get usage stats per voice."""
    dashboard = get_analytics_dashboard(session)
    voice_stats = dashboard.get_voice_usage_stats(user["id"])
    
    return {"voices": voice_stats}


# ============ Performance Analytics ============

@router.get("/performance")
async def get_performance(
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get synthesis performance metrics."""
    dashboard = get_analytics_dashboard(session)
    performance = dashboard.get_synthesis_performance(user["id"])
    
    return performance


@router.get("/errors")
async def get_error_analytics(
    days: int = Query(7, ge=1, le=365),
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get error rate analytics."""
    dashboard = get_analytics_dashboard(session)
    errors = dashboard.get_error_analytics(user["id"], days=days)
    
    return errors


# ============ Quota Analytics ============

@router.get("/quota")
async def get_quota_analytics(
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get quota usage analytics."""
    dashboard = get_analytics_dashboard(session)
    quota = dashboard.get_quota_analytics(user["id"])
    
    if not quota:
        raise HTTPException(status_code=404, detail="User not found")
    
    return quota


# ============ Voice Quality ============

@router.get("/quality/distribution")
async def get_voice_quality_distribution(
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get voice quality score distribution."""
    dashboard = get_analytics_dashboard(session)
    distribution = dashboard.get_voice_quality_scores()
    
    return {"distribution": distribution}


# ============ Marketplace Analytics ============

@router.get("/marketplace/revenue")
async def get_marketplace_revenue(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get marketplace revenue analytics."""
    dashboard = get_analytics_dashboard(session)
    revenue = dashboard.get_marketplace_revenue_analytics(days=days)
    
    return revenue


@router.get("/marketplace/voices")
async def get_marketplace_voice_stats(
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get overall voice marketplace statistics."""
    dashboard = get_analytics_dashboard(session)
    stats = dashboard.get_voice_marketplace_stats()
    
    return stats


# ============ Trends ============

@router.get("/trends")
async def get_usage_trends(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get usage trend indicators."""
    dashboard = get_analytics_dashboard(session)
    trends = dashboard.get_usage_trends(days=days)
    
    return trends


# ============ Dashboard Summary ============

@router.get("/dashboard")
async def get_dashboard_summary(
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Get complete dashboard summary."""
    dashboard = get_analytics_dashboard(session)
    
    return {
        "usage": dashboard.get_user_usage_stats(user["id"], days=30),
        "performance": dashboard.get_synthesis_performance(user["id"]),
        "quota": dashboard.get_quota_analytics(user["id"]),
        "errors": dashboard.get_error_analytics(user["id"], days=7),
        "trends": dashboard.get_usage_trends(days=30),
    }
