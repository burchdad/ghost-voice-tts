"""
Model version management and A/B testing framework.

Enables safe model rollouts, canary testing, and gradual migrations.
"""

import enum
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass

from sqlmodel import SQLModel, Field, Session
import uuid

logger = logging.getLogger(__name__)


class ModelType(str, enum.Enum):
    """TTS model types."""
    TORTOISE = "tortoise"
    VITS = "vits"
    CUSTOM = "custom"


class DeploymentStrategy(str, enum.Enum):
    """Deployment strategies for models."""
    IMMEDIATE = "immediate"  # 100% traffic immediately
    CANARY = "canary"  # Start at X%, monitor, ramp up
    BLUE_GREEN = "blue_green"  # Run both, switch when ready
    GRADUAL = "gradual"  # Linear ramp-up over time


class ModelVersion(SQLModel, table=True):
    """Track TTS model versions and deployments."""
    __tablename__ = "model_versions"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    
    # Version info
    model_type: str  # tortoise, vits, custom
    version_tag: str = Field(unique=True, index=True)  # v1.0.0, v1.1.0, etc
    semantic_version: str  # For proper versioning
    
    # Deployment
    is_active: bool = Field(default=False)  # Currently serving traffic
    traffic_percentage: float = Field(default=0.0)  # 0-100, % of traffic
    deployment_strategy: str = Field(default="canary")
    
    # Performance metrics
    average_latency_ms: Optional[float] = None
    p99_latency_ms: Optional[float] = None
    inference_time_ms: Optional[float] = None
    error_rate: Optional[float] = None
    quality_score: Optional[float] = None  # MOS or similar
    
    # Monitoring thresholds
    max_acceptable_latency_ms: float = Field(default=3000.0)
    max_acceptable_error_rate: float = Field(default=0.05)  # 5%
    min_acceptable_quality_score: float = Field(default=0.7)
    
    # Metadata
    description: str = ""
    trained_on_samples: int = Field(default=0)  # Number of training samples
    training_date: Optional[datetime] = None
    published_date: Optional[datetime] = None
    
    # Relationships
    previous_version_id: Optional[str] = None  # Previous active version
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ABTest(SQLModel, table=True):
    """A/B test configuration for model versions."""
    __tablename__ = "ab_tests"
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    
    # Test configuration
    name: str
    description: Optional[str] = None
    model_version_a: str = Field(foreign_key="model_versions.id")  # Control
    model_version_b: str = Field(foreign_key="model_versions.id")  # Treatment
    
    # Split
    split_percentage: float = Field(default=0.5)  # % of traffic for B
    
    # Duration
    start_date: datetime
    end_date: Optional[datetime] = None
    is_active: bool = Field(default=True)
    
    # Results
    total_samples: int = Field(default=0)
    samples_a: int = Field(default=0)
    samples_b: int = Field(default=0)
    avg_quality_a: Optional[float] = None
    avg_quality_b: Optional[float] = None
    winner: Optional[str] = None  # "a" or "b"
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ModelVersionManager:
    """Manage model versions and deployments."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def create_version(
        self,
        model_type: ModelType,
        version_tag: str,
        semantic_version: str,
        description: str = "",
        trained_on_samples: int = 0,
    ) -> ModelVersion:
        """Create new model version."""
        version = ModelVersion(
            model_type=model_type.value,
            version_tag=version_tag,
            semantic_version=semantic_version,
            description=description,
            trained_on_samples=trained_on_samples,
            training_date=datetime.utcnow(),
        )
        self.session.add(version)
        self.session.commit()
        self.session.refresh(version)
        
        logger.info(f"Created model version: {version_tag}")
        return version
    
    def get_active_version(self, model_type: ModelType) -> Optional[ModelVersion]:
        """Get currently active model version."""
        return self.session.query(ModelVersion).filter(
            ModelVersion.model_type == model_type.value,
            ModelVersion.is_active == True,
            ModelVersion.traffic_percentage >= 100,
        ).first()
    
    def start_canary_deployment(
        self,
        new_version: ModelVersion,
        initial_traffic_percentage: float = 5.0,
        deployment_strategy: DeploymentStrategy = DeploymentStrategy.CANARY,
    ) -> None:
        """Start canary deployment of new model."""
        # Get current active version
        old_version = self.get_active_version(ModelType(new_version.model_type))
        
        if old_version:
            old_version.is_active = False
            old_version.previous_version_id = new_version.id
            self.session.add(old_version)
        
        # Start new version with canary traffic
        new_version.is_active = True
        new_version.traffic_percentage = initial_traffic_percentage
        new_version.deployment_strategy = deployment_strategy.value
        new_version.published_date = datetime.utcnow()
        self.session.add(new_version)
        self.session.commit()
        
        logger.info(
            f"Started canary deployment: {new_version.version_tag} "
            f"at {initial_traffic_percentage}% traffic"
        )
    
    def check_canary_health(
        self,
        version: ModelVersion,
        threshold_latency: float = 3000.0,
        threshold_error_rate: float = 0.05,
    ) -> Dict[str, Any]:
        """Check if canary version is healthy for ramp-up."""
        health = {
            "is_healthy": True,
            "checks": {},
            "recommended_action": "continue",
        }
        
        # Check latency
        if version.p99_latency_ms and version.p99_latency_ms > threshold_latency:
            health["checks"]["latency"] = f"FAILED: {version.p99_latency_ms}ms > {threshold_latency}ms"
            health["is_healthy"] = False
            health["recommended_action"] = "pause"
        else:
            health["checks"]["latency"] = "OK"
        
        # Check error rate
        if version.error_rate and version.error_rate > threshold_error_rate:
            health["checks"]["error_rate"] = f"FAILED: {version.error_rate:.2%} > {threshold_error_rate:.2%}"
            health["is_healthy"] = False
            health["recommended_action"] = "rollback"
        else:
            health["checks"]["error_rate"] = "OK"
        
        # Check quality
        if version.quality_score and version.quality_score < version.min_acceptable_quality_score:
            health["checks"]["quality"] = f"FAILED: {version.quality_score} < {version.min_acceptable_quality_score}"
            health["is_healthy"] = False
            health["recommended_action"] = "rollback"
        else:
            health["checks"]["quality"] = "OK"
        
        return health
    
    def ramp_up_traffic(
        self,
        version: ModelVersion,
        new_percentage: float,
    ) -> None:
        """Increase traffic to canary version."""
        logger.info(f"Ramping up {version.version_tag} to {new_percentage}%")
        version.traffic_percentage = min(new_percentage, 100.0)
        self.session.add(version)
        self.session.commit()
    
    def promote_to_production(self, version: ModelVersion) -> None:
        """Full production migration (100% traffic)."""
        version.traffic_percentage = 100.0
        self.session.add(version)
        self.session.commit()
        
        logger.info(f"Promoted {version.version_tag} to production (100% traffic)")
    
    def rollback_to_previous(self, current_version: ModelVersion) -> Optional[ModelVersion]:
        """Rollback to previous version."""
        if current_version.previous_version_id:
            previous = self.session.query(ModelVersion).filter(
                ModelVersion.id == current_version.previous_version_id
            ).first()
            
            if previous:
                current_version.is_active = False
                previous.is_active = True
                previous.traffic_percentage = 100.0
                
                self.session.add(current_version)
                self.session.add(previous)
                self.session.commit()
                
                logger.warning(f"Rolled back from {current_version.version_tag} to {previous.version_tag}")
                return previous
        
        return None
    
    def create_ab_test(
        self,
        name: str,
        model_version_a: str,
        model_version_b: str,
        split_percentage: float = 0.5,
        description: str = "",
    ) -> ABTest:
        """Create A/B test between two model versions."""
        test = ABTest(
            name=name,
            description=description,
            model_version_a=model_version_a,
            model_version_b=model_version_b,
            split_percentage=split_percentage,
            start_date=datetime.utcnow(),
        )
        self.session.add(test)
        self.session.commit()
        self.session.refresh(test)
        
        logger.info(f"Created A/B test: {name}")
        return test
    
    def end_ab_test(self, test: ABTest, winner: str) -> None:
        """End A/B test and declare winner."""
        test.is_active = False
        test.end_date = datetime.utcnow()
        test.winner = winner
        
        self.session.add(test)
        self.session.commit()
        
        logger.info(f"Ended A/B test {test.name}, winner: {winner}")
    
    def get_deployment_stats(self, version: ModelVersion) -> Dict[str, Any]:
        """Get deployment statistics for version."""
        return {
            "version": version.version_tag,
            "is_active": version.is_active,
            "traffic_percentage": version.traffic_percentage,
            "deployment_strategy": version.deployment_strategy,
            "metrics": {
                "latency_p99_ms": version.p99_latency_ms,
                "error_rate": version.error_rate,
                "quality_score": version.quality_score,
            },
            "training_info": {
                "trained_on_samples": version.trained_on_samples,
                "training_date": version.training_date,
            },
            "health": self.check_canary_health(version),
        }


def get_model_version_manager(session: Session) -> ModelVersionManager:
    """Get or create model version manager."""
    return ModelVersionManager(session)
