"""Seventeen explicit routes; no wildcard proxy and no health endpoints."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_errors import api_error
from app.config import settings
from app.database import get_session
from app.users.dependencies import get_current_user
from app.projects.access import get_project_access, validate_task_assignees
from app.assets.service import get_editor_image
from app.files.storage import get_storage
from .client import OPERATIONS, get_annotation_client, decode_json, invalid_upstream, MAX_JSON
from .service import prepare_creation, record_result
from .validation import CONTRACT, ContractViolation, PUBLIC_CREATE, validate

router = APIRouter(prefix='/api/projects/{project_id}/annotation', tags=['annotation'])


def role_check(role, allowed):
    if role not in allowed:
        raise api_error(403, 'forbidden', 'Project role does not allow this operation')


def parameter_spec(reference):
    if '$ref' in reference:
        return CONTRACT['parameters'][reference['$ref'].rsplit('/', 1)[1]]
    return reference


async def request_body(request):
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise api_error(422, 'validation_error', 'Expected application/json')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_JSON:
            raise api_error(413, 'payload_too_large', 'Request body exceeds 8 MiB')
        raw.extend(chunk)
    try:
        return decode_json(raw)
    except (ValueError, UnicodeError):
        raise api_error(400, 'invalid_json', 'Invalid JSON request') from None


def get_annotation_storage_factory():
    """Defer storage initialization until Go and platform image checks have passed."""
    return get_storage


def make_endpoint(operation):
    is_context = operation.template.endswith('/context')

    async def endpoint(request: Request, session: AsyncSession = Depends(get_session),
                       user=Depends(get_current_user), client=Depends(get_annotation_client),
                       storage_factory=Depends(get_annotation_storage_factory)):
        values = dict(request.path_params)
        query = {}
        try:
            for parameter in operation.parameters:
                spec = parameter_spec(parameter)
                if spec['in'] == 'path':
                    name = spec['name']
                    value = values[name]
                    if spec['schema'].get('type') == 'integer':
                        value = int(value)
                    validate(value, spec['schema'])
                    values[name] = value if name == 'revision' else str(UUID(value))
                elif spec['in'] == 'query' and spec['name'] in request.query_params:
                    name = spec['name']
                    if len(request.query_params.getlist(name)) != 1:
                        raise ContractViolation('Duplicate query parameter')
                    value = request.query_params[name]
                    if spec['schema'].get('type') == 'integer':
                        value = int(value)
                    validate(value, spec['schema'])
                    query[name] = value
            allowed_query = {parameter_spec(p)['name'] for p in operation.parameters if parameter_spec(p)['in'] == 'query'}
            if set(request.query_params) - allowed_query:
                raise ContractViolation('Unknown query parameter')
        except (ContractViolation, ValueError, KeyError):
            raise api_error(422, 'validation_error', 'Invalid path or query parameters') from None
        project_id = values['project_id']
        access = await get_project_access(session, project_id, user, write=operation.method != 'GET')
        role = access.role
        create = operation.method == 'POST' and operation.template == '/tasks'
        assignment = operation.method == 'PATCH'
        if create or assignment or operation.template.startswith('/snapshots'):
            role_check(role, ('admin', 'manager'))
        elif operation.method == 'PUT' or operation.template.endswith('/submit'):
            role_check(role, ('annotator',))
        elif operation.method == 'POST' and operation.template.endswith('/reviews'):
            role_check(role, ('reviewer',))
        body, operation_id = None, None
        if operation.spec.get('requestBody'):
            body = await request_body(request)
            schema = PUBLIC_CREATE if create else operation.spec['requestBody']['content']['application/json']['schema']
            try:
                validate(body, schema)
                # Canonical UUID spellings prevent aliases evading the request key.
                body['request_id'] = str(UUID(body['request_id']))
                if create:
                    body['image_version_ids'] = [str(UUID(x)) for x in body['image_version_ids']]
                    if len(set(body['image_version_ids'])) != len(body['image_version_ids']):
                        raise ContractViolation('Duplicate image IDs')
            except ContractViolation as error:
                raise api_error(422, 'validation_error', 'Invalid annotation request',
                                {'violations': [{'path': '/', 'message': str(error)}]}) from None
        actor_id = user.id
        if create:
            operation_id, body = await prepare_creation(session, project_id, actor_id, body)
        else:
            if assignment:
                await validate_task_assignees(session, project_id, body['assignee_id'], body['reviewer_id'])
            # Release all platform DB read transactions before upstream I/O.
            await session.commit()
        try:
            trace_id = str(UUID(str(getattr(request.state, 'request_id', uuid4()))))
        except ValueError:
            trace_id = str(uuid4())
        try:
            result = await client.request(operation, values, actor_id=actor_id, role=role,
                                          trace_id=trace_id, body=body, query=query)
        except BaseException:
            if operation_id:
                await record_result(session, operation_id)
            raise
        if operation_id:
            await record_result(session, operation_id, result=result)
        if result.status >= 400:
            return JSONResponse(result.data, status_code=result.status)
        if is_context:
            context = result.data
            if context['project_id'] != project_id or context['task_item_id'] != values['task_item_id']:
                raise invalid_upstream()
            image = await get_editor_image(session, project_id, context['image']['image_version_id'])
            if any(str(image[key]) != str(value) for key, value in context['image'].items()):
                raise api_error(502, 'image_metadata_mismatch', 'Annotation image metadata does not match platform')
            await session.commit()
            storage = storage_factory()
            context = dict(context, image_url=storage.view_url(image['object_key'], image['content_type']),
                           image_url_expires_at=(datetime.now(timezone.utc) + timedelta(
                               seconds=settings.S3_DOWNLOAD_EXPIRES_SECONDS)).isoformat().replace('+00:00', 'Z'))
            return JSONResponse(context, headers={'Cache-Control': 'no-store'})
        if result.raw is not None:
            return Response(result.raw, status_code=result.status, media_type='application/json',
                            headers={'X-Content-SHA256': result.sha256})
        return JSONResponse(result.data, status_code=result.status)
    endpoint.__name__ = 'annotation_' + operation.method.lower() + '_' + operation.template.replace('/', '_').replace('{', '').replace('}', '')
    return endpoint


for operation in OPERATIONS:
    router.add_api_route(operation.template, make_endpoint(operation), methods=[operation.method])
