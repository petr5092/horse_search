from typing import Optional
from pydantic import BaseModel
from app.models.user import UserRole


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    user_id: int
    email: str
    full_name: str


class TokenPayload(BaseModel):
    sub: Optional[str] = None  # user email
    user_id: Optional[int] = None
    role: Optional[UserRole] = None
