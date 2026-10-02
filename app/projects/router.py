from typing import List
from fastapi import APIRouter, Depends, status
from app.exception import ProjectNotFoundException
from app.users.models import Users, UserRole
from app.users.dependencies import get_current_user, require_roles
from app.projects.schemas import SProjectCreate, SProjectOut, SProjectUpdate
from app.projects.dao import ProjectDAO

router = APIRouter(
    prefix="/projects",
    tags=["Проекты датасетов"]
)


@router.post("", response_model=SProjectOut, status_code=status.HTTP_201_CREATED, summary="Создать проект (п. В.1 Приложения В)")
def create_project(
    project_data: SProjectCreate,
    current_user: Users = Depends(require_roles([UserRole.ADMIN, UserRole.MANAGER]))
) -> SProjectOut:
    """
    Создание проекта и фиксация схемы разметки (Приложение В, Таблица В.1):
    - target_size по умолчанию = 640 (640x640 px);
    - schema_version по умолчанию = '1.0';
    - Доступно только ролям Администратор и Менеджер (Таблица 3.1).
    """
    new_project = ProjectDAO.add(
        name=project_data.name.strip(),
        schema_version=project_data.schema_version,
        target_size=project_data.target_size,
        description=project_data.description.strip() if project_data.description else None,
        created_by=current_user.id,
    )
    return new_project


@router.get("", response_model=List[SProjectOut], summary="Список всех проектов")
def list_projects(
    current_user: Users = Depends(get_current_user)
) -> List[SProjectOut]:
    """Получение списка всех зарегистрированных проектов датасетов."""
    return ProjectDAO.find_all()


@router.get("/{project_id}", response_model=SProjectOut, summary="Информация о проекте")
def get_project(
    project_id: int,
    current_user: Users = Depends(get_current_user)
) -> SProjectOut:
    """Получение деталей конкретного проекта по ID."""
    project = ProjectDAO.find_by_id(project_id)
    if not project:
        raise ProjectNotFoundException
    return project


@router.patch("/{project_id}", response_model=SProjectOut, summary="Обновление проекта")
def update_project(
    project_id: int,
    project_data: SProjectUpdate,
    current_user: Users = Depends(require_roles([UserRole.ADMIN, UserRole.MANAGER]))
) -> SProjectOut:
    """Обновление метаданных проекта (только Менеджер / Администратор)."""
    project = ProjectDAO.find_by_id(project_id)
    if not project:
        raise ProjectNotFoundException

    update_payload = {k: v for k, v in project_data.model_dump().items() if v is not None}
    updated = ProjectDAO.update_by_id(project_id, **update_payload)
    return updated
