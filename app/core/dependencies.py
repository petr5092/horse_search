import json
from typing import List, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User, UserRole, ROLE_DESCRIPTIONS
from app.models.audit import AuditEvent
from app.core.security import decode_access_token
from app.config import settings

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_PREFIX}/auth/login-form",
    auto_error=False
)


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Недействительный или истекший токен авторизации",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception

    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    email: Optional[str] = payload.get("sub")
    user_id: Optional[int] = payload.get("user_id")
    if email is None or user_id is None:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if user is None:
        raise credentials_exception

    return user


def require_roles(allowed_roles: List[UserRole]):
    """
    Dependency factory to check if the current user has one of the allowed roles.
    Enforces RBAC matrix from Table 3.1 of the report.
    """
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            role_desc = ROLE_DESCRIPTIONS.get(current_user.role, {})
            allowed_titles = [ROLE_DESCRIPTIONS[r]["title"] for r in allowed_roles]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "Недостаточно прав для выполнения операции",
                    "current_role": current_user.role,
                    "current_role_title": role_desc.get("title", current_user.role),
                    "allowed_roles": [r.value for r in allowed_roles],
                    "allowed_titles": allowed_titles,
                    "restriction": role_desc.get("restriction", ""),
                }
            )
        return current_user

    return role_checker


def log_audit(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str,
    actor: Optional[User] = None,
    details: Optional[dict] = None
) -> AuditEvent:
    """Creates an immutable audit log record according to Table 3.5 AuditEvent."""
    event = AuditEvent(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else "system",
        actor_role=actor.role.value if actor else "system",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=json.dumps(details, ensure_ascii=False) if details else None,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
