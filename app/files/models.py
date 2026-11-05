from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, BigInteger

from app.database import Base


class StoredFile(Base):
    __tablename__ = "files"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    original_name = Column(String(255), nullable=False)
    size = Column(BigInteger, nullable=False)
    content_type = Column(String(255), nullable=False)
    staging_key = Column(String(255), nullable=False, unique=True)
    object_key = Column(String(255), nullable=True, unique=True)
    status = Column(String(20), nullable=False, default="pending")
    operation_started_at = Column(DateTime, nullable=True)
    expected_sha256 = Column(String(64), nullable=True)
    sha256 = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
