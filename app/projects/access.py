from dataclasses import dataclass
from sqlalchemy import select
from app.api_errors import api_error
from app.projects.models import Project, ProjectMember, LabelSchemaVersion
from app.users.models import Users, UserRole


@dataclass
class ProjectAccess:
    project: Project
    role: str


async def get_project_access(session, project_id, user, *, write=False):
    if write:
        project = await session.scalar(select(Project).where(Project.id == str(project_id)).with_for_update().execution_options(populate_existing=True))
    else:
        project = await session.get(Project, str(project_id))
    if not user.is_active or not project:
        raise api_error(404, 'not_found', 'Project not found')
    if user.role == UserRole.ADMIN:
        role = 'admin'
    else:
        member = await session.get(ProjectMember, (str(project_id), user.id))
        if not member or not member.is_active:
            raise api_error(404, 'not_found', 'Project not found')
        role = member.role
    if write and project.state != 'active':
        raise api_error(409, 'project_archived', 'Project is archived')
    return ProjectAccess(project, role)


def require_manager(access):
    if access.role not in {'admin', 'manager'}:
        raise api_error(403, 'forbidden', 'Project manager role is required')


async def validate_task_assignees(session, project_id, assignee_id, reviewer_id):
    if assignee_id == reviewer_id:
        raise api_error(422, 'invalid_assignees', 'Assignee and reviewer must differ')
    for user_id, role in [(assignee_id, 'annotator'), (reviewer_id, 'reviewer')]:
        user = await session.get(Users, user_id)
        member = await session.get(ProjectMember, (str(project_id), user_id))
        if not user or not user.is_active or user.role == UserRole.ADMIN or not member or not member.is_active or member.role != role:
            raise api_error(422, 'invalid_assignees', 'Active project annotator and reviewer are required')


async def get_label_schema(session, project_id, version):
    project = await session.get(Project, str(project_id))
    schema = await session.get(LabelSchemaVersion, (project.label_schema_id, version)) if project else None
    if not schema or schema.project_id != str(project_id):
        raise api_error(404, 'not_found', 'Label schema version not found')
    return {'id':schema.schema_id, 'version':schema.version, 'labels':schema.labels}
