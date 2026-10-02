from typing import List, Optional, Dict, Any
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
from app.users.models import UserRole, ROLE_DESCRIPTIONS


def get_token(request: Request) -> str:
    """Extracts token from cookies or Authorization header."""
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

    if not token:
        raise TokenNotFoundException
    return token


def get_current_user(token: str = Depends(get_token)) -> Dict[str, Any]:
    """Decodes JWT and fetches user record via raw SQL."""
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
    if not user or not user.get("is_active"):
        raise UserNotFoundException

    return user


def get_current_admin_user(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Ensures current user is an Administrator."""
    if current_user.get("role") != UserRole.ADMIN.value and current_user.get("role") != UserRole.ADMIN:
        raise ForbiddenException
    return current_user


def require_roles(allowed_roles: List[UserRole]):
    """Checks user role against allowed roles according to Table 3.1."""
    allowed_values = [r.value for r in allowed_roles]

    def role_checker(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        user_role = current_user.get("role")
        if user_role not in allowed_values and user_role not in allowed_roles:
            role_meta = ROLE_DESCRIPTIONS.get(UserRole(user_role), {})
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "Недостаточно прав для выполнения действия",
                    "current_role": user_role,
                    "current_title": role_meta.get("title", ""),
                    "restriction": role_meta.get("restriction", ""),
                    "allowed_roles": allowed_values,
                }
            )
        return current_user

    return role_checker
