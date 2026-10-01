from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_email = Column(String(255), nullable=True)
    actor_role = Column(String(50), nullable=True)
    action = Column(String(100), index=True, nullable=False)
    entity_type = Column(String(50), index=True, nullable=False)  # user, project, asset, batch, version
    entity_id = Column(String(100), index=True, nullable=False)
    details = Column(Text, nullable=True)  # JSON-serialized metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    actor = relationship("User", foreign_keys=[actor_id])

    def __repr__(self):
        return f"<AuditEvent id={self.id} action='{self.action}' actor='{self.actor_email}'>"
