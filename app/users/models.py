import enum
from typing import Optional
from datetime import datetime


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    ANNOTATOR = "annotator"
    REVIEWER = "reviewer"


ROLE_DESCRIPTIONS = {
    UserRole.ADMIN: {
        "title": "Администратор",
        "description": "Пользователи, хранилища, модели",
        "restriction": "Не изменяет эталон без новой версии",
    },
    UserRole.MANAGER: {
        "title": "Менеджер",
        "description": "Проекты, пакеты, экспорт",
        "restriction": "Не удаляет аудит",
    },
    UserRole.ANNOTATOR: {
        "title": "Аннотатор",
        "description": "Создание и правка рамок",
        "restriction": "Не принимает свою работу",
    },
    UserRole.REVIEWER: {
        "title": "Рецензент",
        "description": "Комментарии, приемка, возврат",
        "restriction": "Не меняет сырые файлы",
    },
}
