"""Private, allowlisted Go transport. No user credentials or upstream overrides."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
from uuid import UUID

import httpx
from fastapi import Request

from app.api_errors import api_error
from .validation import CONTRACT, ContractViolation, validate

MAX_JSON = 8 * 1024 * 1024
MAX_SNAPSHOT = 64 * 1024 * 1024


@dataclass(frozen=True)
class Operation:
    method: str
    template: str
    spec: dict
    parameters: list

    @property
    def success_status(self):
        return next(int(code) for code in self.spec['responses'] if code.isdigit() and int(code) < 300)


OPERATIONS = tuple(
    Operation(method.upper(), path.removeprefix('/v1/projects/{project_id}'), spec,
              path_spec.get('parameters', []) + spec.get('parameters', []))
    for path, path_spec in CONTRACT['paths'].items()
    for method, spec in path_spec.items() if method != 'parameters'
)


@dataclass
class UpstreamResult:
    status: int
    data: dict
    raw: bytes | None = None
    sha256: str | None = None


def invalid_upstream():
    return api_error(502, 'upstream_invalid_response', 'Annotation service returned an invalid response')


def decode_json(raw):
    def reject_constant(value):
        raise ValueError('nonfinite JSON')
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    try:
        result = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_keys)
    except RecursionError:
        raise ValueError('JSON nesting is too deep') from None
    # The JSON parser permits lone escaped UTF-16 surrogates. Reject them at
    # the byte boundary before hashing, SQL serialization or response encoding.
    pending = [result]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if '\x00' in value:
                raise ValueError('Invalid JSON Unicode')
            try:
                value.encode('utf-8')
            except UnicodeEncodeError:
                raise ValueError('Invalid JSON Unicode') from None
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return result


class AnnotationClient:
    def __init__(self, client: httpx.AsyncClient, service_token: str, *, snapshot_timeout=60):
        self.client = client
        self.service_token = service_token
        self.snapshot_timeout = snapshot_timeout

    @classmethod
    def from_settings(cls, settings):
        origin = httpx.URL(settings.GO_SERVICE_URL)
        if origin.scheme not in ('http', 'https') or not origin.host or origin.userinfo or origin.query or origin.fragment or origin.path not in ('', '/'):
            raise ValueError('GO_SERVICE_URL must be a fixed HTTP origin')
        token = settings.GO_SERVICE_TOKEN
        if token and ('\r' in token or '\n' in token):
            raise ValueError('Invalid GO_SERVICE_TOKEN')
        client = httpx.AsyncClient(
            base_url=str(origin), follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(connect=settings.GO_CONNECT_TIMEOUT_SECONDS,
                                  read=settings.GO_READ_TIMEOUT_SECONDS,
                                  write=settings.GO_WRITE_TIMEOUT_SECONDS,
                                  pool=settings.GO_POOL_TIMEOUT_SECONDS),
            limits=httpx.Limits(max_connections=settings.GO_MAX_CONNECTIONS),
        )
        return cls(client, token, snapshot_timeout=settings.GO_SNAPSHOT_TIMEOUT_SECONDS)

    async def aclose(self):
        await self.client.aclose()

    @staticmethod
    def _validate_identity(value, operation, values):
        def check_project(node):
            if isinstance(node, dict):
                if 'project_id' in node and node['project_id'] != values['project_id']:
                    raise invalid_upstream()
                for child in node.values():
                    check_project(child)
            elif isinstance(node, list):
                for child in node:
                    check_project(child)
        check_project(value)
        for key in ('task_id', 'task_item_id', 'submission_id'):
            if '{' + key + '}' in operation.template and key in value and value[key] != values[key]:
                raise invalid_upstream()
        resource_id = None
        if operation.template in ('/tasks/{task_id}', '/tasks/{task_id}/assignment'):
            resource_id = values['task_id']
        elif operation.template == '/tasks/{task_id}/submissions/{submission_id}':
            resource_id = values['submission_id']
        elif operation.template == '/snapshots/{snapshot_id}':
            resource_id = values['snapshot_id']
        if resource_id is not None and value.get('id') != resource_id:
            raise invalid_upstream()
        if '{revision}' in operation.template and value.get('revision') != int(values['revision']):
            raise invalid_upstream()

    @asynccontextmanager
    async def _stream(self, request):
        response = await self.client.send(request, stream=True, follow_redirects=False)
        try:
            yield response
        finally:
            await response.aclose()

    async def request(self, operation, path_params, *, actor_id, role, trace_id, body=None, query=None):
        if not self.service_token:
            raise api_error(503, 'temporarily_unavailable', 'Annotation service is not configured')
        if operation not in OPERATIONS or role not in ('admin', 'manager', 'annotator', 'reviewer'):
            raise ValueError('Unapproved annotation operation/context')
        # Callers supply typed IDs, never arbitrary URL fragments.
        project_id = str(UUID(str(path_params['project_id'])))
        values = {'project_id': project_id}
        for name, value in path_params.items():
            if name != 'project_id':
                values[name] = str(int(value)) if name == 'revision' else str(UUID(str(value)))
        path = '/v1/projects/' + project_id + operation.template.format(**values)
        headers = {'Authorization': 'Bearer ' + self.service_token, 'X-Actor-ID': str(actor_id),
                   'X-Actor-Role': role, 'X-Request-ID': str(UUID(str(trace_id))),
                   'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        raw_snapshot = operation.template.endswith('/content')
        timeout = self.client.timeout
        if raw_snapshot or operation.template == '/snapshots':
            timeout = httpx.Timeout(connect=timeout.connect, read=self.snapshot_timeout,
                                    write=timeout.write, pool=timeout.pool)
        try:
            # Explicit Request avoids cookies/default Authorization on an injected client.
            request = httpx.Request(operation.method, self.client.base_url.join(path), headers=headers,
                                    params=query or {}, json=body if body is not None else None,
                                    extensions={'timeout': httpx.Timeout(timeout).as_dict()})
            async with self._stream(request) as response:
                if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise invalid_upstream()
                if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                    raise invalid_upstream()
                limit = MAX_SNAPSHOT if raw_snapshot and response.status_code == 200 else MAX_JSON
                length = response.headers.get('content-length')
                if length is not None:
                    try:
                        if int(length) < 0 or int(length) > limit:
                            raise invalid_upstream()
                    except ValueError:
                        raise invalid_upstream() from None
                data = bytearray()
                # MockTransport commonly returns already-read content; identity ensures
                # those bytes have not been transparently decompressed.
                iterator = response.aiter_raw() if not response.is_stream_consumed else response.aiter_bytes()
                async for chunk in iterator:
                    if len(data) + len(chunk) > limit:
                        raise invalid_upstream()
                    data.extend(chunk)
                raw = bytes(data)
                value = decode_json(raw)
                status = response.status_code
                if status == operation.success_status:
                    schema = operation.spec['responses'][str(status)]['content']['application/json']['schema']
                elif str(status) in operation.spec['responses'] or ('default' in operation.spec['responses'] and 400 <= status <= 599):
                    schema = {'$ref': '#/components/schemas/Error'}
                else:
                    raise invalid_upstream()
                validate(value, schema)
                if raw_snapshot and status == 200:
                    checksum = response.headers.get('X-Content-SHA256')
                    if checksum != hashlib.sha256(raw).hexdigest():
                        raise invalid_upstream()
                    if value['project_id'] != project_id or value['snapshot_id'] != values['snapshot_id']:
                        raise invalid_upstream()
                    return UpstreamResult(status, value, raw, checksum)
                # Object identity is part of the transport contract as well.
                if status < 300:
                    self._validate_identity(value, operation, values)
                return UpstreamResult(status, value)
        except httpx.TimeoutException:
            raise api_error(504, 'upstream_timeout', 'Annotation service timed out') from None
        except httpx.TransportError:
            raise api_error(502, 'upstream_unavailable', 'Annotation service is unavailable') from None
        except (ContractViolation, ValueError, UnicodeError):
            raise invalid_upstream() from None


def get_annotation_client(request: Request) -> AnnotationClient:
    return request.app.state.annotation_client
