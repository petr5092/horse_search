from sqlalchemy import select
from app.api_errors import api_error
from app.assets.models import ImageVersion
from app.files.models import StoredFile


async def lock_ready_file(session, file_id, user=None):
    file = await session.scalar(select(StoredFile).where(StoredFile.id == str(file_id)).with_for_update().execution_options(populate_existing=True))
    if not file or (user is not None and user.role != 'admin' and file.owner_id != user.id):
        raise api_error(404, 'not_found', 'File not found')
    if file.status != 'ready' or not file.object_key or not file.sha256:
        raise api_error(409, 'file_not_ready', 'A ready immutable file is required')
    return file


async def resolve_image_contexts(session, project_id, image_version_ids, *, lock=False):
    ids = [str(i) for i in image_version_ids]
    if len(set(ids)) != len(ids) or not ids:
        raise api_error(422, 'invalid_images', 'Images must be nonempty and unique')
    query = select(ImageVersion, StoredFile).join(StoredFile, ImageVersion.file_id == StoredFile.id).where(
        ImageVersion.project_id == str(project_id), ImageVersion.id.in_(ids), ImageVersion.status == 'ready', StoredFile.status == 'ready')
    if lock:
        query = query.with_for_update()
    rows = {image.id:(image,file) for image,file in (await session.execute(query)).all()}
    if len(rows) != len(ids):
        raise api_error(404, 'not_found', 'Ready project image not found')
    results = []
    for image_id in ids:
        image, file = rows[image_id]
        if image.sha256 != file.sha256 or not file.object_key or file.content_type not in {'image/png','image/jpeg','image/webp'}:
            raise api_error(409, 'image_integrity_error', 'Working image metadata does not match its file')
        results.append({'image_version_id':image.id,'sha256':image.sha256,'width':image.width,'height':image.height})
    return results


async def get_editor_image(session, project_id, image_version_id):
    context = (await resolve_image_contexts(session, project_id, [image_version_id]))[0]
    image = await session.get(ImageVersion, str(image_version_id))
    file = await session.get(StoredFile, image.file_id)
    return {**context, 'object_key':file.object_key, 'content_type':file.content_type}
