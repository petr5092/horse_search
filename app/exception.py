from fastapi import HTTPException, status

UserAlreadyExistsException = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="Пользователь с таким email уже существует",
)

IncorrectEmailOrPasswordException = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Неверная почта или пароль",
)

TokenExpiredException = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Срок действия токена истек",
)

TokenNotFoundException = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Токен авторизации не найден",
)

IncorrectTokenFormatException = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Некорректный формат токена",
)

UserNotFoundException = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Пользователь не найден",
)

ForbiddenException = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Недостаточно прав для выполнения действия",
)

CannotDemoteLastAdminException = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="Нельзя изменить роль последнего действующего администратора системы",
)

ProjectNotFoundException = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Проект не найден",
)
