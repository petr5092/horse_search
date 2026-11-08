from datetime import datetime
from uuid import uuid4
from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, JSON, DateTime, CheckConstraint, UniqueConstraint
from app.database import Base


class Project(Base):
    __tablename__ = 'projects'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name = Column(String(255), nullable=False)
    state = Column(String(16), nullable=False, default='active')
    label_schema_id = Column(String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    current_label_schema_version = Column(Integer, nullable=False, default=1)
    __table_args__ = (CheckConstraint("state IN ('active','archived')"),)


class ProjectMember(Base):
    __tablename__ = 'project_members'
    project_id = Column(String(36), ForeignKey('projects.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    role = Column(String(16), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    __table_args__ = (CheckConstraint("role IN ('manager','annotator','reviewer')"),)


class LabelSchemaVersion(Base):
    __tablename__ = 'label_schema_versions'
    schema_id = Column(String(36), primary_key=True)
    version = Column(Integer, primary_key=True)
    project_id = Column(String(36), ForeignKey('projects.id'), nullable=False)
    labels = Column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint('project_id', 'version'), CheckConstraint('version > 0'))
