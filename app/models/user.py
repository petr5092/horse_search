import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, func
from app.database import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    ANNOTATOR = "annotator"
    REVIEWER = "reviewer"


ROLE_DESCRIPTIONS = {
    UserRole.ADMIN: {
        "title": "Администратор",
        "description": "Пользователи, хранилища, модели",
        "restriction": "Не изменяет эталон без новой версии",
    },
    UserRole.MANAGER: {
        "title": "Менеджер",
        "description": "Проекты, пакеты, экспорт",
        "restriction": "Не удаляет аудит",
    },
    UserRole.ANNOTATOR: {
        "title": "Аннотатор",
        "description": "Создание и правка рамок",
        "restriction": "Не принимает свою работу",
    },
    UserRole.REVIEWER: {
        "title": "Рецензент",
        "description": "Комментарии, приемка, возврат",
        "restriction": "Не меняет сырые файлы",
    },
}


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.ANNOTATOR, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<User id={self.id} email='{self.email}' role='{self.role}'>"
