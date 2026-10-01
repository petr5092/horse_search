from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User, UserRole, ROLE_DESCRIPTIONS
from app.models.audit import AuditEvent
from app.schemas.user import UserResponse, UserRoleUpdateRequest, AuditEventResponse, RoleInfo
from app.core.dependencies import get_current_user, require_roles, log_audit

router = APIRouter(prefix="/users", tags=["Управление пользователями и ролями"])


def attach_role_info(user: User) -> UserResponse:
    role_meta = ROLE_DESCRIPTIONS.get(user.role, {
        "title": user.role.value,
        "description": "",
        "restriction": "",
    })
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        role_info=RoleInfo(
            role=user.role,
            title=role_meta["title"],
            description=role_meta["description"],
            restriction=role_meta["restriction"],
        ),
    )


@router.get("", response_model=List[UserResponse], summary="Список сотрудников")
def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    role: Optional[UserRole] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.MANAGER]))
):
    """
    Получение списка пользователей системы.
    Доступно ролям: **Администратор**, **Менеджер**.
    """
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    users = query.offset(skip).limit(limit).all()
    return [attach_role_info(u) for u in users]


@router.get("/{user_id}", response_model=UserResponse, summary="Получить пользователя по ID")
def get_user_by_id(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.MANAGER]))
):
    """Информация о конкретном пользователе."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь с ID {user_id} не найден",
        )
    return attach_role_info(user)


@router.patch("/{user_id}/role", response_model=UserResponse, summary="Назначение роли сотруднику (только Администратор)")
def update_user_role(
    user_id: int,
    payload: UserRoleUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.ADMIN]))
):
    """
    Изменение роли сотрудника:
    - **Строго ограничено:** доступно только роли **Администратор** (Таблица 3.1 реферата).
    - Каждое изменение фиксируется в неизменяемом журнале аудита (`AuditEvent`).
    - Защита: Администратор не может понизить сам себя, если он единственный администратор в системе.
    """
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь с ID {user_id} не найден",
        )

    # Safety check: prevent removing the last administrator
    if target_user.id == current_user.id and payload.new_role != UserRole.ADMIN:
        admin_count = db.query(User).filter(User.role == UserRole.ADMIN, User.is_active == True).count()
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Нельзя изменить роль последнего действующего администратора системы",
            )

    old_role = target_user.role
    target_user.role = payload.new_role
    db.commit()
    db.refresh(target_user)

    log_audit(
        db=db,
        action="USER_ROLE_CHANGED",
        entity_type="user",
        entity_id=str(target_user.id),
        actor=current_user,
        details={
            "target_user_id": target_user.id,
            "target_user_email": target_user.email,
            "old_role": old_role.value,
            "new_role": payload.new_role.value,
            "admin_actor": current_user.email,
        },
    )

    return attach_role_info(target_user)


@router.get("/audit/logs", response_model=List[AuditEventResponse], summary="Журнал аудита пользователей")
def get_user_audit_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.MANAGER]))
):
    """
    Просмотр записей аудита действий пользователей.
    Доступно ролям: **Администратор**, **Менеджер**.
    """
    events = (
        db.query(AuditEvent)
        .order_by(AuditEvent.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return events
