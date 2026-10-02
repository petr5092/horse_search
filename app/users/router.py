from typing import List, Dict, Any
from fastapi import APIRouter, Response, Depends, status
from app.exception import (
    UserAlreadyExistsException,
    IncorrectEmailOrPasswordException,
    UserNotFoundException,
    CannotDemoteLastAdminException,
)
from app.users.models import UserRole, ROLE_DESCRIPTIONS
from app.users.schemas import (
    SUserRegister,
    SUserAuth,
    SUserOut,
    SUserRoleUpdate,
    SRoleInfo,
)
from app.users.dao import UserDAO
from app.users.auth import get_password_hash, authenticate_user, create_access_token
from app.users.dependencies import get_current_user, get_current_admin_user

router = APIRouter(
    prefix="/auth",
    tags=["Аутентификация & Пользователи"]
)


def build_user_out(user: Dict[str, Any]) -> SUserOut:
    role_enum = UserRole(user["role"])
    role_meta = ROLE_DESCRIPTIONS.get(role_enum, {
        "title": role_enum.value,
        "description": "",
        "restriction": "",
    })
    return SUserOut(
        id=user["id"],
        email=user["email"],
        full_name=user["full_name"],
        role=role_enum,
        is_active=bool(user["is_active"]),
        created_at=user["created_at"],
        role_info=SRoleInfo(
            role=role_enum,
            title=role_meta["title"],
            description=role_meta["description"],
            restriction=role_meta["restriction"],
        ),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, summary="Регистрация пользователя")
def register_user(user_data: SUserRegister) -> SUserOut:
    """
    Чистый синхронный SQL-запрос для регистрации пользователя:
    - Проверка наличия записи через SELECT;
    - Определение роли (первый пользователь = admin, остальные = annotator);
    - Хеширование пароля и выполнение INSERT;
    - Возврат созданной записи.
    """
    existing_user = UserDAO.find_one_or_none(email=user_data.email.lower().strip())
    if existing_user:
        raise UserAlreadyExistsException

    total_users = UserDAO.count()
    assigned_role = UserRole.ADMIN if total_users == 0 else UserRole.ANNOTATOR

    hashed_password = get_password_hash(user_data.password)
    new_user = UserDAO.add(
        email=user_data.email.lower().strip(),
        full_name=user_data.full_name.strip(),
        hashed_password=hashed_password,
        role=assigned_role.value,
        is_active=True,
    )
    return build_user_out(new_user)


@router.post("/login", summary="Авторизация пользователя")
def login_user(response: Response, user_data: SUserAuth):
    """
    Аутентификация пользователя (чистый SQL):
    - Поиск пользователя по email через SELECT;
    - Проверка bcrypt-хеша пароля;
    - Генерация JWT токена и установка в HttpOnly cookie.
    """
    user = authenticate_user(user_data.email, user_data.password)
    if not user:
        raise IncorrectEmailOrPasswordException

    access_token = create_access_token({"sub": str(user["id"]), "role": user["role"]})
    response.set_cookie("access_token", access_token, httponly=True)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": build_user_out(user),
    }


@router.post("/logout", summary="Выход из системы")
def logout_user(response: Response):
    """Сброс авторизационной куки."""
    response.delete_cookie("access_token")
    return {"status": "ok", "message": "Успешный выход из системы"}


@router.get("/me", response_model=SUserOut, summary="Профиль текущего пользователя")
def get_me(current_user: Dict[str, Any] = Depends(get_current_user)) -> SUserOut:
    """Получение профиля текущего пользователя."""
    return build_user_out(current_user)


@router.get("/roles", response_model=List[SRoleInfo], summary="Справочник ролей системы (Таблица 3.1)")
def get_roles():
    """Справочник ролей ВИМ: права и регламентные ограничения."""
    return [
        SRoleInfo(
            role=role,
            title=meta["title"],
            description=meta["description"],
            restriction=meta["restriction"],
        )
        for role, meta in ROLE_DESCRIPTIONS.items()
    ]


@router.get("/all", response_model=List[SUserOut], summary="Все сотрудники (только Администратор)")
def get_all_users(current_user: Dict[str, Any] = Depends(get_current_admin_user)) -> List[SUserOut]:
    """Список всех пользователей через SELECT * FROM users (только для Администратора)."""
    users = UserDAO.find_all()
    return [build_user_out(u) for u in users]


@router.patch("/{user_id}/role", response_model=SUserOut, summary="Назначение роли (только Администратор)")
def update_user_role(
    user_id: int,
    role_data: SUserRoleUpdate,
    current_user: Dict[str, Any] = Depends(get_current_admin_user)
) -> SUserOut:
    """
    Изменение роли пользователя через UPDATE users SET role = :new_role:
    - Доступно только Администратору;
    - Защита от снятия роли с последнего администратора системы.
    """
    target_user = UserDAO.find_by_id(user_id)
    if not target_user:
        raise UserNotFoundException

    if target_user["id"] == current_user["id"] and role_data.new_role.value != UserRole.ADMIN.value:
        admin_count = UserDAO.count(role=UserRole.ADMIN.value)
        if admin_count <= 1:
            raise CannotDemoteLastAdminException

    updated_user = UserDAO.update_by_id(user_id, role=role_data.new_role.value)
    return build_user_out(updated_user)
