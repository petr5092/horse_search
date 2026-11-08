"""Swagger describes the public user-authenticated API, derived from Go DTOs."""
from copy import deepcopy
import json
import re

from fastapi import FastAPI
from fastapi.openapi.models import OpenAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.annotation_gateway.client import OPERATIONS
from app.annotation_gateway.openapi import install_annotation_openapi, PREFIX
from app.annotation_gateway.router import router
from app.annotation_gateway.validation import _SPEC, PUBLIC_CREATE


class Label(BaseModel):
    platform_value: str


class Task(BaseModel):
    label: Label


def application():
    app=FastAPI()
    app.include_router(router)
    @app.post('/platform-tasks',response_model=Task)
    def platform_task(body: Task):
        return body
    return app


def document():
    app=application()
    install_annotation_openapi(app)
    return app,app.openapi()


def schema_for(doc,operation,part):
    value=doc['paths'][PREFIX+operation.template][operation.method.lower()]
    if part=='request':
        reference=value['requestBody']['content']['application/json']['schema']['$ref']
    else:
        reference=value['responses'][str(operation.success_status)]['content']['application/json']['schema']['$ref']
    return doc['components']['schemas'][reference.rsplit('/',1)[1]]


def test_public_openapi_all_operations_parameters_and_model_validation():
    before=deepcopy(_SPEC)
    app,doc=document()
    annotation_paths={p:v for p,v in doc['paths'].items() if p.startswith(PREFIX)}
    assert sum(len(methods) for methods in annotation_paths.values())==17
    operation_ids=[]
    for operation in OPERATIONS:
        path=PREFIX+operation.template
        published=doc['paths'][path][operation.method.lower()]
        operation_ids.append(published['operationId'])
        declared={p['name'] for p in published['parameters'] if p['in']=='path'}
        assert declared==set(re.findall(r'{([^}]+)}',path))
        assert all(p['required'] is True for p in published['parameters'] if p['in']=='path')
        assert all(p['in'] in ('path','query') for p in published['parameters'])
        expected_query={'cursor','limit'} if operation.template in (
            '/tasks','/tasks/{task_id}/items','/task-items/{task_item_id}/annotations/revisions',
            '/tasks/{task_id}/submissions') and operation.method=='GET' else set()
        assert {p['name'] for p in published['parameters'] if p['in']=='query'}==expected_query
        assert str(operation.success_status) in published['responses']
        assert '502' in published['responses'] and '504' in published['responses']
        if operation.spec.get('requestBody'):
            assert published['requestBody']['required'] is True
            assert schema_for(doc,operation,'request')['type']=='object'
        security=published['security']
        assert security==[{'AnnotationUserBearer':[]},{'AnnotationSessionCookie':[]},{'AnnotationLegacySessionCookie':[]}]
    assert len(set(operation_ids))==17
    OpenAPI.model_validate(doc)
    assert _SPEC==before,'Generating public metadata must not alter the canonical Go document'
    assert app.openapi() is doc


def test_create_body_is_public_schema_and_context_includes_signed_url_fields():
    app,doc=document()
    create=next(o for o in OPERATIONS if o.method=='POST' and o.template=='/tasks')
    public_create=schema_for(doc,create,'request')
    assert public_create['properties']==PUBLIC_CREATE['properties']
    assert set(public_create['required'])==set(PUBLIC_CREATE['required'])
    assert public_create['additionalProperties'] is False
    assert 'label_schema' not in public_create['properties'] and 'items' not in public_create['properties']
    for example in public_create['examples']:
        assert set(example)==set(PUBLIC_CREATE['required'])
        assert example['image_version_ids'] and example['label_schema_version']==1
    context_operation=next(o for o in OPERATIONS if o.template.endswith('/context'))
    context=schema_for(doc,context_operation,'response')
    assert {'image_url','image_url_expires_at'}<=set(context['required'])
    assert context['properties']['image_url']['format']=='uri'
    assert context['properties']['image_url_expires_at']['format']=='date-time'
    assert context['additionalProperties'] is False
    response=doc['paths'][PREFIX+context_operation.template]['get']['responses']['200']
    assert response['headers']['Cache-Control']['schema']['const']=='no-store'
    for sample in response['content']['application/json']['examples'].values():
        assert sample['value']['image_url'].startswith('http://localhost:')
        assert sample['value']['image_url_expires_at'].endswith('Z')
    for template,method,expected in (
        ('/task-items/{task_item_id}/annotations','put',{'request_id','expected_revision','result','boxes'}),
        ('/tasks/{task_id}/submit','post',{'request_id','expected_task_revision','items'})):
        operation=next(o for o in OPERATIONS if o.template==template and o.method.lower()==method)
        assert set(schema_for(doc,operation,'request')['required'])==expected


def test_refs_resolve_components_do_not_collide_and_credentials_are_user_only():
    app,doc=document()
    def check(node):
        if isinstance(node,dict):
            if '$ref' in node:
                reference=node['$ref']
                assert reference.startswith('#/'),'No external unresolved references'
                target=doc
                for segment in reference[2:].split('/'):
                    target=target[segment.replace('~1','/').replace('~0','~')]
            for value in node.values():check(value)
        elif isinstance(node,list):
            for value in node:check(value)
    check(doc)
    schemas=doc['components']['schemas']
    assert schemas['Label']['properties']=={'platform_value':{'title':'Platform Value','type':'string'}}
    assert schemas['Task']['properties']['label']['$ref']=='#/components/schemas/Label'
    assert set(schemas['AnnotationLabel']['properties'])=={'id','name'}
    assert 'task_revision' in schemas['AnnotationTask']['properties']
    text=json.dumps(doc)
    assert 'ServiceToken' not in text and 'X-Actor-ID' not in text and 'X-Actor-Role' not in text
    assert doc['components']['securitySchemes']['AnnotationUserBearer']['bearerFormat']=='JWT'
    assert doc['components']['securitySchemes']['AnnotationSessionCookie']['name']=='access_token'
    assert '/healthz' not in doc['paths'] and '/readyz' not in doc['paths']


def test_installation_replaces_cached_schema_and_is_idempotent():
    app=application()
    old=app.openapi()
    assert 'requestBody' not in old['paths'][PREFIX+'/tasks']['post']
    install_annotation_openapi(app)
    doc=app.openapi()
    install_annotation_openapi(app)
    assert app.openapi() is doc
    assert doc is not old
    assert 'requestBody' in doc['paths'][PREFIX+'/tasks']['post']


def test_actual_fastapi_openapi_endpoint_includes_annotation_contract():
    # No lifespan/network is needed to render the actual app's public API metadata.
    from app.main import app
    response=TestClient(app).get('/openapi.json')
    assert response.status_code==200
    doc=response.json()
    OpenAPI.model_validate(doc)
    assert 'requestBody' in doc['paths'][PREFIX+'/tasks']['post']
    assert doc['components']['schemas']['AnnotationTaskItemContext']['properties']['image_url']
