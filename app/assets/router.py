from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from app.api_errors import api_error
from app.database import get_session
from app.projects.access import get_project_access, require_manager
from app.projects.router import Input
from app.users.dependencies import get_current_user
from app.assets.models import Asset, ImageVersion
from app.assets.service import lock_ready_file

router = APIRouter(prefix='/api/projects/{project_id}',tags=['Assets'])

class AssetInput(Input):
    source_file_id: UUID
    source_uri: str = Field(min_length=1,max_length=2048)
    license: str = Field(min_length=1,max_length=255)
    session_id: UUID

def asset_json(asset):
    return {key:getattr(asset,key) for key in ['id','project_id','source_file_id','source_uri','license','session_id']}

@router.post('/assets',status_code=201)
async def create_asset(project_id: UUID,payload: AssetInput,user=Depends(get_current_user),session=Depends(get_session)):
    async with session.begin():
        require_manager(await get_project_access(session,project_id,user,write=True))
        await lock_ready_file(session,payload.source_file_id,user)
        asset = Asset(project_id=str(project_id),**{k:str(v) for k,v in payload.model_dump().items()})
        session.add(asset)
        await session.flush()
    return asset_json(asset)

@router.get('/assets')
async def assets(project_id: UUID,user=Depends(get_current_user),session=Depends(get_session)):
    require_manager(await get_project_access(session,project_id,user))
    return [asset_json(a) for a in (await session.scalars(select(Asset).where(Asset.project_id == str(project_id)).order_by(Asset.id))).all()]

@router.get('/image-versions/{image_version_id}')
async def image_version(project_id: UUID,image_version_id: UUID,user=Depends(get_current_user),session=Depends(get_session)):
    require_manager(await get_project_access(session,project_id,user))
    image = await session.get(ImageVersion,str(image_version_id))
    if not image or image.project_id != str(project_id):
        raise api_error(404,'not_found','Image version not found')
    return {k:getattr(image,k) for k in ['id','project_id','asset_id','file_id','sha256','width','height','status','created_at']}
