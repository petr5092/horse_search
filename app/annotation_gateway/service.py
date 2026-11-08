"""Durably prepare creation before issuing any network operation."""
import hashlib
import json
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.api_errors import api_error
from app.assets.service import resolve_image_contexts
from app.projects.access import get_label_schema, validate_task_assignees
from .models import GatewayOperation, GatewayOperationImage, now
from .validation import validate_named


def canonical_hash(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


async def prepare_creation(session, project_id, actor_id, body):
    digest = canonical_hash(body)
    key = (GatewayOperation.project_id == project_id, GatewayOperation.actor_id == actor_id,
           GatewayOperation.operation == 'create_task', GatewayOperation.request_id == body['request_id'])
    previous = await session.scalar(select(GatewayOperation).where(*key))
    if previous is not None:
        if previous.public_hash != digest:
            raise api_error(409, 'idempotency_conflict', 'request_id already used with a different request')
        payload, operation_id = previous.go_payload, previous.id
        await session.commit()
        return operation_id, payload
    await validate_task_assignees(session, project_id, body['assignee_id'], body['reviewer_id'])
    schema = await get_label_schema(session, project_id, body['label_schema_version'])
    images = await resolve_image_contexts(session, project_id, body['image_version_ids'], lock=True)
    payload = {k: body[k] for k in ('request_id', 'title', 'assignee_id', 'reviewer_id')}
    payload.update(label_schema=schema, items=images)
    validate_named(payload, 'CreateTaskRequest')
    operation = GatewayOperation(project_id=project_id, actor_id=actor_id, operation='create_task',
                                 request_id=body['request_id'], public_hash=digest, go_payload=payload)
    session.add(operation)
    try:
        await session.flush()
        for image in images:
            session.add(GatewayOperationImage(operation_id=operation.id, image_version_id=image['image_version_id']))
        await session.commit()
    except IntegrityError:
        # A concurrent request won the unique key. Rollback discards our candidate
        # and its pins; only the winner's immutable payload can be sent.
        await session.rollback()
        previous = await session.scalar(select(GatewayOperation).where(*key))
        if previous is None:
            raise
        if previous.public_hash != digest:
            raise api_error(409, 'idempotency_conflict', 'request_id already used with a different request')
        operation_id, payload = previous.id, previous.go_payload
        await session.commit()
        return operation_id, payload
    return operation.id, payload


async def record_result(session, operation_id, result=None):
    # Persist even uncertain HTTP outcomes. Never release image references here.
    status = 'unknown'
    external_id = None
    if result is not None:
        status = 'succeeded' if result.status == 201 else 'rejected'
        external_id = result.data.get('id') if result.status == 201 else None
    values = dict(status=status, updated_at=now())
    if external_id:
        values['external_id'] = external_id
    # A concurrent failed replay must not downgrade an already confirmed success.
    await session.execute(update(GatewayOperation).where(
        GatewayOperation.id == operation_id,
        GatewayOperation.status != 'succeeded',
    ).values(**values))
    await session.commit()
