"""Platform-owned command journal; never reads annotation schema tables."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from app.database import Base


def now():
    return datetime.now(timezone.utc)


class GatewayOperation(Base):
    __tablename__ = 'gateway_operations'
    __table_args__ = (
        UniqueConstraint('project_id', 'actor_id', 'operation', 'request_id', name='uq_gateway_operation_key'),
        CheckConstraint("status in ('prepared','unknown','succeeded','rejected')", name='ck_gateway_operation_status'),
    )
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(String(36), ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False)
    actor_id = Column(Integer, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    operation = Column(String(64), nullable=False)
    request_id = Column(String(36), nullable=False)
    public_hash = Column(String(64), nullable=False)
    go_payload = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False, default='prepared')
    external_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now, onupdate=now)


class GatewayOperationImage(Base):
    __tablename__ = 'gateway_operation_images'
    operation_id = Column(String(36), ForeignKey('gateway_operations.id', ondelete='RESTRICT'), primary_key=True)
    image_version_id = Column(String(36), ForeignKey('image_versions.id', ondelete='RESTRICT'), primary_key=True)
