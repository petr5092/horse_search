import logging
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import async_session_maker
from app.files.models import StoredFile
from app.files.schemas import DownloadResponse, FileResponse, UploadRequest, UploadResponse
from app.files.storage import InvalidUpload, get_storage
from app.users.dependencies import get_current_user
from app.users.models import UserRole

router = APIRouter(prefix="/files", tags=["Файлы"])
logger = logging.getLogger(__name__)
S3_ERRORS = (BotoCoreError, ClientError)


async def get_session():
    async with async_session_maker() as session:
        yield session


def storage_error(exc):
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return HTTPException(409, "Upload has not reached storage; upload the file first")
        if code in {"412", "PreconditionFailed"}:
            return HTTPException(409, "Upload changed during completion; retry")
    return HTTPException(503, "File storage is temporarily unavailable")


async def accessible_file(file_id, user, session):
    file = await session.get(StoredFile, str(file_id))
    if not file or (file.owner_id != user.id and user.role != UserRole.ADMIN):
        raise HTTPException(404, "File not found")
    return file


def stale(file):
    return (file.operation_started_at is not None and
            file.operation_started_at < datetime.utcnow() - timedelta(seconds=settings.S3_OPERATION_LEASE_SECONDS))


async def claim(file, status, session, **values):
    # The timestamp fences late workers after a stale claim is taken over.
    started = datetime.utcnow()
    result = await session.execute(update(StoredFile).where(
        StoredFile.id == file.id, StoredFile.status == file.status,
        StoredFile.operation_started_at == file.operation_started_at,
    ).values(status=status, operation_started_at=started, **values)
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await session.rollback()
        raise HTTPException(409, "File is being changed; retry")
    await session.commit()
    await session.refresh(file)
    return started


async def finish(file_id, claimed_status, started, session, **values):
    result = await session.execute(update(StoredFile).where(
        StoredFile.id == file_id, StoredFile.status == claimed_status,
        StoredFile.operation_started_at == started,
    ).values(operation_started_at=None, **values).execution_options(synchronize_session=False))
    await session.commit()
    return result.rowcount == 1


@router.post("/uploads", response_model=UploadResponse, status_code=201)
async def request_upload(payload: UploadRequest, user=Depends(get_current_user),
                         session: AsyncSession = Depends(get_session), storage=Depends(get_storage)):
    if payload.size > settings.S3_MAX_FILE_SIZE:
        raise HTTPException(413, "File exceeds the configured size limit")
    file_id = str(uuid4())
    key = f"staging/{user.id}/{file_id}"
    try:
        form = await run_in_threadpool(storage.upload_form, key, payload.size, payload.content_type)
    except S3_ERRORS as exc:
        raise storage_error(exc) from exc
    file = StoredFile(id=file_id, owner_id=user.id, original_name=payload.original_name,
                      size=payload.size, content_type=payload.content_type, staging_key=key,
                      expected_sha256=payload.sha256.lower() if payload.sha256 else None, status="pending")
    session.add(file)
    await session.commit()
    return UploadResponse(file=FileResponse.model_validate(file), **form,
                          expires_in=settings.S3_UPLOAD_EXPIRES_SECONDS)


@router.get("", response_model=list[FileResponse])
async def list_files(offset: int = 0, limit: int = 50, user=Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    if offset < 0 or limit < 1 or limit > 100:
        raise HTTPException(422, "Use offset >= 0 and limit between 1 and 100")
    return (await session.scalars(select(StoredFile).where(StoredFile.owner_id == user.id)
        .order_by(StoredFile.created_at.desc(), StoredFile.id).offset(offset).limit(limit))).all()


@router.post("/{file_id}/complete", response_model=FileResponse)
async def complete_upload(file_id: UUID, user=Depends(get_current_user),
                          session: AsyncSession = Depends(get_session), storage=Depends(get_storage)):
    file = await accessible_file(file_id, user, session)
    if file.status == "ready":
        return file
    if file.status != "pending" and not (file.status == "finalizing" and stale(file)):
        raise HTTPException(409, "File is being changed; retry")
    old_key = file.object_key
    final_key = f"originals/{file.owner_id}/{file.id}/{uuid4()}"
    started = await claim(file, "finalizing", session, object_key=final_key)
    if old_key:
        try:
            await run_in_threadpool(storage.delete, old_key)
        except S3_ERRORS as exc:
            await finish(file.id, "finalizing", started, session,
                         status="pending", object_key=old_key)
            raise storage_error(exc) from exc
    try:
        checksum = await run_in_threadpool(storage.finalize, file, final_key)
    except (InvalidUpload,) + S3_ERRORS as exc:
        cleaned = False
        try:
            await run_in_threadpool(storage.delete, final_key)
            cleaned = True
        except S3_ERRORS:
            logger.warning("Could not clean up incomplete original %s", file.id)
        await finish(file.id, "finalizing", started, session, status="pending",
                     object_key=None if cleaned else final_key)
        if isinstance(exc, InvalidUpload):
            raise HTTPException(409, str(exc)) from exc
        raise storage_error(exc) from exc
    accepted = await finish(file.id, "finalizing", started, session, status="ready", sha256=checksum)
    if not accepted:
        try:
            await run_in_threadpool(storage.delete, final_key)
        except S3_ERRORS:
            logger.warning("Could not clean up superseded original %s", file.id)
        raise HTTPException(409, "Completion was superseded; retry")
    await session.refresh(file)
    # Cleanup is best effort: a still-valid POST can recreate staging anyway.
    try:
        await run_in_threadpool(storage.delete, file.staging_key)
    except S3_ERRORS:
        logger.warning("Could not clean up staging for %s", file.id)
    return file


@router.get("/{file_id}/download", response_model=DownloadResponse)
async def download_file(file_id: UUID, user=Depends(get_current_user),
                        session: AsyncSession = Depends(get_session), storage=Depends(get_storage)):
    file = await accessible_file(file_id, user, session)
    if file.status != "ready":
        raise HTTPException(409, "File is not ready")
    try:
        url = await run_in_threadpool(storage.download_url, file.object_key, file.original_name)
    except S3_ERRORS as exc:
        raise storage_error(exc) from exc
    return DownloadResponse(url=url, expires_in=settings.S3_DOWNLOAD_EXPIRES_SECONDS)


@router.delete("/{file_id}", status_code=204)
async def delete_file(file_id: UUID, user=Depends(get_current_user),
                      session: AsyncSession = Depends(get_session), storage=Depends(get_storage)):
    file = await accessible_file(file_id, user, session)
    if file.status not in {"pending", "ready"} and not (file.status == "deleting" and stale(file)):
        raise HTTPException(409, "File is being changed; retry")
    started = await claim(file, "deleting", session)
    try:
        await run_in_threadpool(storage.delete, file.staging_key, file.object_key)
    except S3_ERRORS as exc:
        # A network error may arrive after S3 deleted the original. Never expose
        # a potentially missing object as ready; let DELETE retry immediately.
        await session.execute(update(StoredFile).where(
            StoredFile.id == file.id, StoredFile.status == "deleting",
            StoredFile.operation_started_at == started,
        ).values(operation_started_at=datetime.utcnow() - timedelta(
            seconds=settings.S3_OPERATION_LEASE_SECONDS + 1))
            .execution_options(synchronize_session=False))
        await session.commit()
        raise storage_error(exc) from exc
    result = await session.execute(delete(StoredFile).where(
        StoredFile.id == file.id, StoredFile.status == "deleting",
        StoredFile.operation_started_at == started,
    ).execution_options(synchronize_session=False))
    await session.commit()
    if result.rowcount != 1:
        raise HTTPException(409, "Deletion was superseded; retry")
    return Response(status_code=204)
