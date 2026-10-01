from datetime import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, EmailStr, Field
from app.models.user import UserRole, ROLE_DESCRIPTIONS


class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=150, description="ФИО сотрудника ВИМ")


class UserRegisterRequest(UserBase):
    password: str = Field(..., min_length=6, max_length=100, description="Пароль минимум 6 символов")


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class RoleInfo(BaseModel):
    role: UserRole
    title: str
    description: str
    restriction: str


class UserResponse(UserBase):
    id: int
    role: UserRole
    is_active: bool
    created_at: datetime
    role_info: Optional[RoleInfo] = None

    class Config:
        from_attributes = True


class UserRoleUpdateRequest(BaseModel):
    new_role: UserRole = Field(..., description="Новая роль сотрудника по Таблице 3.1")


class AuditEventResponse(BaseModel):
    id: int
    actor_email: Optional[str] = None
    actor_role: Optional[str] = None
    action: str
    entity_type: str
    entity_id: str
    details: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
