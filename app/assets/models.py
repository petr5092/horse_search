from datetime import datetime
from uuid import uuid4
from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from app.database import Base


class Asset(Base):
    __tablename__ = 'assets'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(String(36), ForeignKey('projects.id'), nullable=False, index=True)
    source_file_id = Column(String(36), ForeignKey('files.id'), nullable=False)
    source_uri = Column(String(2048), nullable=False)
    license = Column(String(255), nullable=False)
    session_id = Column(String(36), nullable=False)
    __table_args__ = (UniqueConstraint('id', 'project_id'),)


class ImageVersion(Base):
    __tablename__ = 'image_versions'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(String(36), ForeignKey('projects.id'), nullable=False, index=True)
    asset_id = Column(String(36), nullable=False)
    file_id = Column(String(36), ForeignKey('files.id'), nullable=False, unique=True)
    sha256 = Column(String(64), nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default='ready')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (ForeignKeyConstraint(['asset_id','project_id'], ['assets.id','assets.project_id']),
                      CheckConstraint('width > 0 AND height > 0'), CheckConstraint("status = 'ready'"))
