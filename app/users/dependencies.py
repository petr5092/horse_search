from datetime import datetime, timezone
from typing import List, Optional
from fastapi import Request, Depends, HTTPException, status
import jwt
from app.config import settings
from app.exception import (
    TokenNotFoundException,
    TokenExpiredException,
    IncorrectTokenFormatException,
    UserNotFoundException,
    ForbiddenException,
)
from app.users.dao import UserDAO
from app.users.models import Users, UserRole, ROLE_DESCRIPTIONS


def get_token(request: Request) -> str:
    """Gets token from cookie or Authorization header."""
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

    if not token:
        raise TokenNotFoundException
    return token


def get_current_user(token: str = Depends(get_token)) -> Users:
    """Decodes JWT token and retrieves the current user synchronously."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise TokenExpiredException
    except jwt.PyJWTError:
        raise IncorrectTokenFormatException

    user_id: Optional[str] = payload.get("sub")
    if not user_id:
        raise UserNotFoundException

    user = UserDAO.find_by_id(int(user_id))
    if not user:
        raise UserNotFoundException

    return user


def get_current_admin_user(current_user: Users = Depends(get_current_user)) -> Users:
    """Verifies that the user is an Administrator."""
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenException
    return current_user


def require_roles(allowed_roles: List[UserRole]):
    """Checks that the user has one of the allowed roles according to Table 3.1."""
    def role_checker(current_user: Users = Depends(get_current_user)) -> Users:
        if current_user.role not in allowed_roles:
            role_meta = ROLE_DESCRIPTIONS.get(current_user.role, {})
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "Недостаточно прав для выполнения действия",
                    "current_role": current_user.role,
                    "current_title": role_meta.get("title", ""),
                    "restriction": role_meta.get("restriction", ""),
                    "allowed_roles": [r.value for r in allowed_roles],
                }
            )
        return current_user
    return role_checker
