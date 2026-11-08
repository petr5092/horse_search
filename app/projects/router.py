from __future__ import annotations
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal, Optional
from sqlalchemy import select
from app.api_errors import api_error
from app.database import get_session
from app.projects.models import Project, ProjectMember, LabelSchemaVersion
from app.projects.access import get_project_access, require_manager, get_label_schema
from app.users.dependencies import get_current_user
from app.users.models import Users, UserRole

router = APIRouter(prefix='/api/projects', tags=['Projects'])

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    @field_validator('*', mode='before', check_fields=False)
    @classmethod
    def database_safe_text(cls, value):
        if isinstance(value, str):
            if '\x00' in value:
                raise ValueError('Text must not contain NUL characters')
            try:
                value.encode('utf-8')
            except UnicodeEncodeError:
                raise ValueError('Text must contain valid UTF-8 characters') from None
        return value

class ProjectCreate(Input):
    name: str = Field(min_length=1, max_length=255)
    manager_id: int = Field(gt=0)

class ProjectPatch(Input):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    state: Optional[Literal['active','archived']] = None

class MemberInput(Input):
    role: Literal['manager','annotator','reviewer']
    is_active: bool = True

class Label(Input):
    id: str = Field(min_length=1,max_length=64)
    name: str = Field(min_length=1,max_length=100)

class SchemaInput(Input):
    labels: list[Label] = Field(min_length=1,max_length=100)
    @model_validator(mode='after')
    def unique_labels(self):
        if len({label.id for label in self.labels}) != len(self.labels):
            raise ValueError('Label IDs must be unique')
        return self


def project_json(project):
    return {key:getattr(project,key) for key in ['id','name','state','label_schema_id','current_label_schema_version']}

def member_json(member):
    return {key:getattr(member,key) for key in ['project_id','user_id','role','is_active']}

@router.post('',status_code=201)
async def create_project(payload: ProjectCreate, user=Depends(get_current_user), session=Depends(get_session)):
    if user.role != UserRole.ADMIN:
        raise api_error(403,'forbidden','Only an administrator can create projects')
    async with session.begin():
        manager = await session.get(Users,payload.manager_id)
        if not manager or not manager.is_active:
            raise api_error(422,'invalid_manager','Active manager user is required')
        project = Project(id=str(uuid4()),name=payload.name,label_schema_id=str(uuid4()),current_label_schema_version=1,state='active')
        session.add(project)
        await session.flush()
        session.add(ProjectMember(project_id=project.id,user_id=manager.id,role='manager'))
        session.add(LabelSchemaVersion(schema_id=project.label_schema_id,version=1,project_id=project.id,labels=[{'id':'horse','name':'Лошадь'}]))
    return project_json(project)

@router.get('')
async def list_projects(user=Depends(get_current_user), session=Depends(get_session)):
    query = select(Project)
    if user.role != UserRole.ADMIN:
        query = query.join(ProjectMember).where(ProjectMember.user_id == user.id,ProjectMember.is_active.is_(True))
    return [project_json(project) for project in (await session.scalars(query.order_by(Project.id))).all()]

@router.get('/{project_id}')
async def read_project(project_id: UUID,user=Depends(get_current_user),session=Depends(get_session)):
    return project_json((await get_project_access(session,project_id,user)).project)

@router.patch('/{project_id}')
async def patch_project(project_id: UUID,payload: ProjectPatch,user=Depends(get_current_user),session=Depends(get_session)):
    async with session.begin():
        await session.scalar(select(Project).where(Project.id == str(project_id)).with_for_update())
        access = await get_project_access(session,project_id,user)
        require_manager(access)
        if access.project.state == 'archived' and (payload.state != 'active' or payload.name is not None):
            raise api_error(409,'project_archived','Restore the project before editing it')
        for key,value in payload.model_dump(exclude_none=True).items():
            setattr(access.project,key,value)
    return project_json(access.project)

@router.get('/{project_id}/members')
async def members(project_id: UUID,user=Depends(get_current_user),session=Depends(get_session)):
    require_manager(await get_project_access(session,project_id,user))
    return [member_json(m) for m in (await session.scalars(select(ProjectMember).where(ProjectMember.project_id == str(project_id)).order_by(ProjectMember.user_id))).all()]

@router.put('/{project_id}/members/{user_id}')
async def put_member(project_id: UUID,user_id: int,payload: MemberInput,user=Depends(get_current_user),session=Depends(get_session)):
    async with session.begin():
        await session.scalar(select(Project).where(Project.id == str(project_id)).with_for_update())
        require_manager(await get_project_access(session,project_id,user,write=True))
        target = await session.get(Users,user_id)
        if not target or not target.is_active:
            raise api_error(422,'invalid_member','Active user is required')
        member = await session.get(ProjectMember,(str(project_id),user_id))
        if not member:
            member = ProjectMember(project_id=str(project_id),user_id=user_id)
            session.add(member)
        member.role,member.is_active = payload.role,payload.is_active
    return member_json(member)

@router.delete('/{project_id}/members/{user_id}',status_code=204)
async def delete_member(project_id: UUID,user_id: int,user=Depends(get_current_user),session=Depends(get_session)):
    async with session.begin():
        await session.scalar(select(Project).where(Project.id == str(project_id)).with_for_update())
        require_manager(await get_project_access(session,project_id,user,write=True))
        member = await session.get(ProjectMember,(str(project_id),user_id))
        if not member:
            raise api_error(404,'not_found','Member not found')
        member.is_active=False
    return Response(status_code=204)

@router.get('/{project_id}/label-schema-versions')
async def schemas(project_id: UUID,user=Depends(get_current_user),session=Depends(get_session)):
    await get_project_access(session,project_id,user)
    rows = (await session.scalars(select(LabelSchemaVersion).where(LabelSchemaVersion.project_id == str(project_id)).order_by(LabelSchemaVersion.version))).all()
    return [{'id':s.schema_id,'version':s.version,'labels':s.labels} for s in rows]

@router.post('/{project_id}/label-schema-versions',status_code=201)
async def add_schema(project_id: UUID,payload: SchemaInput,user=Depends(get_current_user),session=Depends(get_session)):
    async with session.begin():
        await session.scalar(select(Project).where(Project.id == str(project_id)).with_for_update())
        access = await get_project_access(session,project_id,user,write=True)
        require_manager(access)
        if access.project.current_label_schema_version >= 2147483647:
            raise api_error(409,'schema_version_exhausted','Label schema version limit reached')
        access.project.current_label_schema_version += 1
        schema = LabelSchemaVersion(schema_id=access.project.label_schema_id,version=access.project.current_label_schema_version,project_id=str(project_id),labels=[l.model_dump() for l in payload.labels])
        session.add(schema)
    return {'id':schema.schema_id,'version':schema.version,'labels':schema.labels}
