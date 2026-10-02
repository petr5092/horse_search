from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field
from app.users.models import UserRole


class SUserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, description="Пароль минимум 6 символов")
    full_name: str = Field(..., min_length=2, max_length=150, description="ФИО сотрудника ВИМ")


class SUserAuth(BaseModel):
    email: EmailStr
    password: str


class SRoleInfo(BaseModel):
    role: UserRole
    title: str
    description: str
    restriction: str


class SUserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    role_info: Optional[SRoleInfo] = None

    class Config:
        from_attributes = True


class SUserRoleUpdate(BaseModel):
    new_role: UserRole = Field(..., description="Новая роль сотрудника по Таблице 3.1")


class SToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
