from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User, UserRole, ROLE_DESCRIPTIONS
from app.schemas.user import UserRegisterRequest, UserLoginRequest, UserResponse, RoleInfo
from app.schemas.token import Token
from app.core.security import get_password_hash, verify_password, create_access_token
from app.core.dependencies import get_current_user, log_audit

router = APIRouter(prefix="/auth", tags=["Аутентификация и регистрация"])


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


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED, summary="Регистрация нового пользователя")
def register_user(
    payload: UserRegisterRequest,
    db: Session = Depends(get_db)
):
    """
    Регистрация нового сотрудника:
    - Первый зарегистрированный пользователь автоматически получает роль **Администратора** (`admin`).
    - Все последующие пользователи по умолчанию получают роль **Аннотатора** (`annotator`).
    - Создается неизменяемая запись в журнале аудита.
    """
    existing_user = db.query(User).filter(User.email == payload.email.lower()).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Пользователь с email '{payload.email}' уже зарегистрирован в системе",
        )

    # First user becomes Admin, otherwise Annotator
    total_users = db.query(User).count()
    assigned_role = UserRole.ADMIN if total_users == 0 else UserRole.ANNOTATOR

    new_user = User(
        email=payload.email.lower().strip(),
        full_name=payload.full_name.strip(),
        hashed_password=get_password_hash(payload.password),
        role=assigned_role,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    log_audit(
        db=db,
        action="USER_REGISTERED",
        entity_type="user",
        entity_id=str(new_user.id),
        actor=new_user,
        details={
            "email": new_user.email,
            "full_name": new_user.full_name,
            "assigned_role": new_user.role.value,
            "is_initial_admin": total_users == 0,
        },
    )

    return attach_role_info(new_user)


@router.post("/login", response_model=Token, summary="Авторизация (JSON)")
def login_json(
    payload: UserLoginRequest,
    db: Session = Depends(get_db)
):
    """
    Авторизация по email и паролю с возвратом JWT-токена доступа.
    """
    user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Учетная запись деактивирована администратором",
        )

    access_token = create_access_token(
        subject=user.email,
        user_id=user.id,
        role=user.role.value
    )

    log_audit(
        db=db,
        action="USER_LOGGED_IN",
        entity_type="user",
        entity_id=str(user.id),
        actor=user,
        details={"ip": "client_session"},
    )

    return Token(
        access_token=access_token,
        token_type="bearer",
        role=user.role,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
    )


@router.post("/login-form", response_model=Token, summary="OAuth2 авторизация для Swagger UI")
def login_oauth2_form(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """Эндпоинт для кнопки 'Authorize' в Swagger UI документации."""
    user = db.query(User).filter(User.email == form_data.username.lower().strip()).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        subject=user.email,
        user_id=user.id,
        role=user.role.value
    )

    return Token(
        access_token=access_token,
        token_type="bearer",
        role=user.role,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
    )


@router.get("/me", response_model=UserResponse, summary="Текущий профиль и права")
def get_me(current_user: User = Depends(get_current_user)):
    """
    Возвращает информацию о текущем авторизованном пользователе,
    включая его активную роль и действующие регламентные ограничения (Таблица 3.1).
    """
    return attach_role_info(current_user)


@router.get("/roles", response_model=List[RoleInfo], summary="Справочник ролей системы (Таблица 3.1)")
def list_system_roles():
    """Справочник доступных ролей с описанием прав и ограничений по регламенту ВИМ."""
    return [
        RoleInfo(
            role=role,
            title=meta["title"],
            description=meta["description"],
            restriction=meta["restriction"],
        )
        for role, meta in ROLE_DESCRIPTIONS.items()
    ]
