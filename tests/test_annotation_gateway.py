"""Gateway integration on isolated platform tables and fixed Go HTTP responses."""
import asyncio
from copy import deepcopy
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api_errors import install_api_errors
from app.database import Base, get_session
from app.projects.models import Project, ProjectMember, LabelSchemaVersion
from app.assets.models import Asset, ImageVersion
from app.files.models import StoredFile
from app.files.storage import get_storage
from app.users.models import Users, UserRole
from app.users.dependencies import get_current_user
from app.annotation_gateway.client import AnnotationClient, OPERATIONS, get_annotation_client, decode_json
from app.annotation_gateway.models import GatewayOperation, GatewayOperationImage
from app.annotation_gateway.router import router, get_annotation_storage_factory
from app.annotation_gateway.validation import CONTRACT, SCHEMAS, ContractViolation, validate, validate_named

PROJECT = '00000000-0000-4000-8000-000000000001'
ITEM = '00000000-0000-4000-8000-000000000003'
IMAGE = '00000000-0000-4000-8000-000000000004'
SNAPSHOT = '00000000-0000-4000-8000-000000000009'
PREFIX = f'/api/projects/{PROJECT}/annotation'


def example(name):
    # Examples remain in the reviewed original contract, not hand-maintained mocks.
    contract = json.loads(Path('docs/contracts/annotation-service.openapi.json').read_text())
    return deepcopy(contract['components']['schemas'][name]['examples'][0])


def op(method, template):
    return next(o for o in OPERATIONS if o.method == method and o.template == template)


def call_transport(handler, operation=None, **kwargs):
    async def run():
        async with httpx.AsyncClient(base_url='http://annotation.internal', transport=httpx.MockTransport(handler),
                                     headers={'Authorization':'Bearer user-default'}, cookies={'secret':'bad'}) as http:
            client = AnnotationClient(http, 'service-secret')
            return await client.request(operation or op('GET', '/tasks'),
                {'project_id':PROJECT, 'snapshot_id':SNAPSHOT, **kwargs.pop('path', {})},
                actor_id=2, role='manager', trace_id=str(uuid4()), **kwargs)
    return asyncio.run(run())


@pytest.fixture
def api(tmp_path):
    pg_url = os.environ.get('S1_TEST_GATEWAY_DATABASE_URL')
    if pg_url:
        from sqlalchemy.engine import make_url
        target = make_url(pg_url)
        assert target.host in ('localhost', '127.0.0.1') and target.database == 'horse_s1_test_gateway', 'Only the designated local gateway test DB may be reset'
    engine = create_async_engine(pg_url or f'sqlite+aiosqlite:///{tmp_path / "gateway.db"}', poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def setup():
        async with engine.begin() as connection:
            if pg_url:
                await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            session.add(Project(id=PROJECT, name='P', label_schema_id=example('LabelSchema')['id']))
            await session.flush()
            for i, role in [(1,'admin'),(2,'manager'),(3,'annotator'),(4,'reviewer'),(5,'annotator')]:
                session.add(Users(id=i,email=f'{i}@test.example', hashed_password='unused', full_name=str(i), role=UserRole(role), is_active=True))
                await session.flush()
                if i > 1 and i != 5:
                    session.add(ProjectMember(project_id=PROJECT,user_id=i,role=role))
            schema = example('LabelSchema')
            session.add(LabelSchemaVersion(schema_id=schema['id'], version=1, project_id=PROJECT, labels=schema['labels']))
            session.add(StoredFile(id='file',owner_id=2, original_name='working.png',size=1,content_type='image/png',
                staging_key='stage',object_key='working/key.png',status='ready',sha256='a'*64))
            await session.flush()
            session.add(Asset(id='asset',project_id=PROJECT,source_file_id='file',source_uri='synthetic',license='test',session_id=str(uuid4())))
            await session.flush()
            session.add(ImageVersion(id=IMAGE,project_id=PROJECT,asset_id='asset',file_id='file',sha256='a'*64,width=640,height=640))
            await session.commit()
    asyncio.run(setup())
    active_sessions = []
    request_session = ContextVar("gateway_request_session")
    async def session_dependency():
        async with sessions() as session:
            active_sessions.append(session)
            context_token = request_session.set(session)
            try:
                yield session
            finally:
                active_sessions.remove(session)
                request_session.reset(context_token)
    async def current_user(request: Request):
        async with sessions() as session:
            return await session.get(Users,int(request.headers.get('test-user', '2')))
    received, urls = [], []
    state = SimpleNamespace(response=example('Task'),status=201,error=None,raw=None)
    async def handle(request):
        assert not request_session.get().in_transaction(), 'Request DB transaction held during Go I/O'
        received.append(request)
        if state.error:
            raise state.error('test', request=request)
        if state.raw is not None:
            return httpx.Response(state.status,content=state.raw,headers={'Content-Type':'application/json'})
        return httpx.Response(state.status,json=deepcopy(state.response))
    transport = httpx.AsyncClient(base_url='http://annotation.internal', transport=httpx.MockTransport(handle))
    gateway = AnnotationClient(transport,'private-service-token')
    class Storage:
        def view_url(self,key,content_type):
            urls.append((key,content_type))
            return 'http://localhost:9000/working.png?temporary=1'
    app = FastAPI()
    install_api_errors(app)
    app.include_router(router)
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_annotation_client] = lambda:gateway
    app.dependency_overrides[get_annotation_storage_factory] = lambda:Storage
    with TestClient(app) as client:
        yield SimpleNamespace(app=app,client=client,sessions=sessions,state=state,received=received,urls=urls,
            body=dict(request_id=str(uuid4()),title='Task',assignee_id=3,reviewer_id=4,
                      label_schema_version=1,image_version_ids=[IMAGE]))
    asyncio.run(gateway.aclose())
    asyncio.run(engine.dispose())


def mutate_db(api, callback):
    async def run():
        async with api.sessions() as session:
            await callback(session)
            await session.commit()
    asyncio.run(run())


def test_explicit_allowlist_and_contract_examples():
    assert len(OPERATIONS) == len(router.routes) == 17
    assert all('*' not in route.path and not route.path.endswith(('healthz','readyz')) for route in router.routes)
    for name in SCHEMAS:
        for sample in json.loads(Path('docs/contracts/annotation-service.openapi.json').read_text())['components']['schemas'][name].get('examples',[]):
            validate_named(sample,name)


@pytest.mark.parametrize('operation', OPERATIONS, ids=lambda o:o.method+' '+o.template)
def test_transport_exact_paths_and_shapes(operation):
    params={'project_id':PROJECT,'task_id':example('Task')['id'],'task_item_id':ITEM,
            'snapshot_id':SNAPSHOT,'submission_id':example('Submission')['id'],'revision':1}
    status = operation.success_status
    content = operation.spec['responses'][str(status)]['content']['application/json']
    sample = next(iter(content['examples'].values()))['value']
    body = None
    if operation.spec.get('requestBody'):
        body = next(iter(operation.spec['requestBody']['content']['application/json']['examples'].values()))['value']
    def handler(request):
        assert request.url.path == '/v1/projects/' + PROJECT + operation.template.format(**params)
        assert request.method == operation.method
        assert request.headers['authorization'] == 'Bearer service-secret'
        assert request.headers['x-actor-id'] == '2' and request.headers['x-actor-role'] == 'manager'
        assert request.headers.get('cookie') is None
        assert request.headers['host'] == 'annotation.internal'
        assert request.headers['accept-encoding'] == 'identity'
        assert request.extensions['timeout']['connect'] == 5
        if operation.template.endswith('/content') or operation.template == '/snapshots':
            assert request.extensions['timeout']['read'] == 60
        assert (json.loads(request.content) if request.content else None) == body
        raw = json.dumps(sample,indent=1).encode()
        headers={'Content-Type':'application/json'}
        if operation.template.endswith('/content'):
            headers['X-Content-SHA256']=hashlib.sha256(raw).hexdigest()
        return httpx.Response(status,content=raw,headers=headers)
    result=call_transport(handler,operation,path=params,body=body)
    assert result.status == status and result.data == sample


def test_actor_spoof_and_authoritative_creation_frozen_retry(api):
    headers={'X-Actor-ID':'1','X-Actor-Role':'admin','Authorization':'Bearer user-jwt','Cookie':'user=secret','Host':'attacker.invalid'}
    first=api.client.post(PREFIX+'/tasks',json=api.body,headers=headers)
    assert first.status_code==201,first.text
    upstream=api.received[0]
    assert upstream.headers['x-actor-id']=='2' and upstream.headers['x-actor-role']=='manager'
    assert upstream.headers['authorization']=='Bearer private-service-token' and 'cookie' not in upstream.headers
    prepared=json.loads(upstream.content)
    assert prepared['label_schema']==example('LabelSchema') and prepared['items']==[example('ImageContext')]
    async def change(session):
        schema=await session.get(LabelSchemaVersion,(example('LabelSchema')['id'],1))
        schema.labels=[{'id':'changed','name':'Changed'}]
    mutate_db(api,change)
    assert api.client.post(PREFIX+'/tasks',json=api.body).status_code==201
    assert api.received[0].content==api.received[1].content
    conflict=api.client.post(PREFIX+'/tasks',json={**api.body,'title':'different'})
    assert conflict.status_code==409 and len(api.received)==2
    async def inspect(session):
        operation=await session.scalar(select(GatewayOperation))
        assert operation.status=='succeeded' and operation.external_id==first.json()['id']
        assert (await session.scalar(select(GatewayOperationImage))).image_version_id==IMAGE
    mutate_db(api,inspect)


def test_uncertain_result_pins_survive_and_retry_authorizes_current_project(api):
    api.state.error=httpx.ReadTimeout
    response=api.client.post(PREFIX+'/tasks',json=api.body)
    assert response.status_code==504 and response.json()['error']['code']=='upstream_timeout'
    async def inspect(session):
        assert (await session.scalar(select(GatewayOperation))).status=='unknown'
        assert await session.scalar(select(GatewayOperationImage)) is not None
        member=await session.get(ProjectMember,(PROJECT,2));member.is_active=False
    mutate_db(api,inspect)
    api.state.error=None
    assert api.client.post(PREFIX+'/tasks',json=api.body).status_code==404
    assert len(api.received)==1


@pytest.mark.parametrize('extra', [{'width':640},{'label_schema':{}},{'image_version_ids':[IMAGE,IMAGE]}, {'assignee_id':True}])
def test_create_rejects_browser_metadata_or_bad_ids(api,extra):
    assert api.client.post(PREFIX+'/tasks',json={**api.body,**extra}).status_code==422
    assert not api.received


@pytest.mark.parametrize('user', [3,4,5])
def test_creation_requires_project_manager(api,user):
    assert api.client.post(PREFIX+'/tasks',json=api.body,headers={'test-user':str(user)}).status_code in (403,404)
    assert not api.received


def test_assignment_checks_active_target_roles(api):
    body=dict(request_id=str(uuid4()),expected_task_revision=1,assignee_id=5,reviewer_id=4)
    response=api.client.patch(PREFIX+'/tasks/'+example('Task')['id']+'/assignment',json=body)
    assert response.status_code==422 and not api.received


def test_context_only_signs_exact_project_image_after_go_permission(api):
    api.state.status=200;api.state.response=example('TaskItemContext')
    response=api.client.get(PREFIX+f'/task-items/{ITEM}/context',headers={'test-user':'3'})
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    assert response.json()['image_url'].startswith('http://localhost:9000/')
    assert response.json()['image_url_expires_at'].endswith('Z')
    assert api.urls==[('working/key.png','image/png')]
    api.urls.clear()
    api.state.response['image']['width']=641
    assert api.client.get(PREFIX+f'/task-items/{ITEM}/context',headers={'test-user':'3'}).status_code==502
    assert not api.urls
    api.state.response=example('Error');api.state.status=404
    assert api.client.get(PREFIX+f'/task-items/{ITEM}/context',headers={'test-user':'3'}).status_code==404
    assert not api.urls
    api.state.status=200;api.state.response=example('TaskItemContext');api.state.response['project_id']=str(uuid4())
    assert api.client.get(PREFIX+f'/task-items/{ITEM}/context',headers={'test-user':'3'}).status_code==502
    assert not api.urls


@pytest.mark.parametrize('path', ['/tasks?limit=101','/tasks?cursor=','/tasks?url=http://evil','/tasks?limit=1&limit=2',f'/task-items/{ITEM}/annotations/revisions/0','/tasks/not-a-uuid'])
def test_invalid_parameters_never_reach_go(api,path):
    assert api.client.get(PREFIX+path).status_code==422
    assert not api.received


def test_forward_conflict_details_unmodified(api):
    api.state.status=409;api.state.response=example('Error')
    response=api.client.get(PREFIX+'/tasks')
    assert response.status_code==409 and response.json()==api.state.response


@pytest.mark.parametrize('error,expected,code',[(httpx.ConnectError,502,'upstream_unavailable'),(httpx.ConnectTimeout,504,'upstream_timeout')])
def test_transport_failure_mapping(error,expected,code):
    def handler(request):raise error('test',request=request)
    with pytest.raises(Exception) as caught:call_transport(handler)
    assert caught.value.status_code==expected and caught.value.detail['code']==code


@pytest.mark.parametrize('response', [httpx.Response(200,json={}),httpx.Response(302,headers={'location':'http://evil'}),
    httpx.Response(200,content=b'{broken',headers={'content-type':'application/json'}),
    httpx.Response(200,content=b'{"items":[],"items":[],"next_cursor":null}',headers={'content-type':'application/json'}),
    httpx.Response(200,stream=httpx.ByteStream(b'invalid'),headers={'content-type':'application/json','content-encoding':'gzip'})])
def test_malformed_upstream_never_becomes_success(response):
    with pytest.raises(Exception) as caught:call_transport(lambda request:response)
    assert caught.value.status_code==502


def test_snapshot_preserves_exact_raw_bytes_and_checks_digest_and_size(monkeypatch):
    sample=example('SnapshotContent')
    raw=(json.dumps(sample,indent=3)+'\n').encode()
    operation=op('GET','/snapshots/{snapshot_id}/content')
    def response(checksum):return httpx.Response(200,content=raw,headers={'Content-Type':'application/json','X-Content-SHA256':checksum})
    result=call_transport(lambda request:response(hashlib.sha256(raw).hexdigest()),operation)
    assert result.raw==raw and result.sha256==hashlib.sha256(raw).hexdigest()
    with pytest.raises(Exception) as caught:call_transport(lambda request:response('f'*64),operation)
    assert caught.value.status_code==502
    monkeypatch.setattr('app.annotation_gateway.client.MAX_SNAPSHOT',len(raw)-1)
    with pytest.raises(Exception) as caught:call_transport(lambda request:response(hashlib.sha256(raw).hexdigest()),operation)
    assert caught.value.status_code==502


@pytest.mark.skipif(not os.environ.get('S1_TEST_GATEWAY_DATABASE_URL'), reason='Explicit isolated local PostgreSQL opt-in required')
def test_postgres_concurrent_public_creation_freezes_one_command(api,monkeypatch):
    import importlib
    routing = importlib.import_module("app.annotation_gateway.router")
    original = routing.get_project_access
    async def concurrent_requests():
        # Start both public requests before either acquires the project write lock.
        # The real access helper serializes preparation; no lock is bypassed.
        arrived = 0
        barrier = asyncio.Event()
        async def access_barrier(*args,**kwargs):
            nonlocal arrived
            arrived += 1
            if arrived == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(),5)
            return await original(*args,**kwargs)
        monkeypatch.setattr(routing,'get_project_access',access_barrier)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://localhost') as public:
            first, second = await asyncio.gather(
                public.post(PREFIX+'/tasks',json=api.body),
                public.post(PREFIX+'/tasks',json=api.body))
            assert first.status_code == second.status_code == 201,(first.text,second.text)
            assert first.json()==second.json()
            assert len(api.received)==2 and api.received[0].content==api.received[1].content
            async with api.sessions() as session:
                operations=(await session.scalars(select(GatewayOperation))).all()
                pins=(await session.scalars(select(GatewayOperationImage))).all()
                assert len(operations)==len(pins)==1
                assert operations[0].status=='succeeded'
                assert operations[0].go_payload==json.loads(api.received[0].content)
                schema=await session.get(LabelSchemaVersion,(example('LabelSchema')['id'],1))
                schema.labels=[{'id':'new','name':'New'}]
                await session.commit()
            replay=await public.post(PREFIX+'/tasks',json=api.body)
            assert replay.status_code==201
            assert api.received[2].content==api.received[0].content
            conflict=await public.post(PREFIX+'/tasks',json={**api.body,'title':'changed'})
            assert conflict.status_code==409 and len(api.received)==3
    asyncio.run(concurrent_requests())


@pytest.mark.skipif(not os.environ.get('S1_TEST_GATEWAY_DATABASE_URL'), reason='Explicit isolated local PostgreSQL opt-in required')
def test_postgres_unknown_network_result_resolved_by_public_replay(api):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://localhost') as public:
            api.state.error=httpx.ReadTimeout
            unknown=await public.post(PREFIX+'/tasks',json=api.body)
            assert unknown.status_code==504
            async with api.sessions() as session:
                assert (await session.scalar(select(GatewayOperation))).status=='unknown'
                assert len((await session.scalars(select(GatewayOperationImage))).all())==1
            api.state.error=None
            recovered=await public.post(PREFIX+'/tasks',json=api.body)
            assert recovered.status_code==201
            assert api.received[0].content==api.received[1].content
            async with api.sessions() as session:
                operation=await session.scalar(select(GatewayOperation))
                assert operation.status=='succeeded' and operation.external_id==recovered.json()['id']
    asyncio.run(run())


def test_snapshot_stream_is_bounded_without_content_length(monkeypatch):
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'x'*10
            yield b'y'*10
            raise AssertionError('The gateway must stop consuming an oversized stream')
    monkeypatch.setattr('app.annotation_gateway.client.MAX_SNAPSHOT',15)
    response=httpx.Response(200,stream=Chunks(),headers={'Content-Type':'application/json','X-Content-SHA256':'a'*64})
    with pytest.raises(Exception) as caught:
        call_transport(lambda request:response,op('GET','/snapshots/{snapshot_id}/content'))
    assert caught.value.status_code==502


def test_upstream_unconfigured_fails_closed_without_http():
    async def run():
        def unexpected(request):raise AssertionError('Unconfigured service must never receive HTTP')
        async with httpx.AsyncClient(base_url='http://annotation.internal',transport=httpx.MockTransport(unexpected)) as http:
            client=AnnotationClient(http,None)
            with pytest.raises(Exception) as caught:
                await client.request(op('GET','/tasks'),{'project_id':PROJECT},actor_id=2,role='manager',trace_id=str(uuid4()))
            assert caught.value.status_code==503 and caught.value.detail['code']=='temporarily_unavailable'
    asyncio.run(run())


def test_response_wrong_resource_identity_is_not_success():
    wrong=example('Task');wrong['id']=str(uuid4())
    with pytest.raises(Exception) as caught:
        call_transport(lambda request:httpx.Response(200,json=wrong),op('GET','/tasks/{task_id}'),path={'task_id':example('Task')['id']})
    assert caught.value.status_code==502


def test_nonfinite_number_validation_handles_huge_json_integer():
    with pytest.raises(ContractViolation):
        validate(10**500,{'type':'number'})


def test_context_denied_or_mismatched_does_not_initialize_storage(api):
    from fastapi import HTTPException
    calls=[]
    def no_credentials():
        calls.append(True)
        raise HTTPException(503,'Storage credentials unavailable')
    api.app.dependency_overrides[get_annotation_storage_factory]=lambda:no_credentials
    api.state.status=404;api.state.response=example('Error')
    denied=api.client.get(PREFIX+f'/task-items/{ITEM}/context',headers={'test-user':'3'})
    assert denied.status_code==404 and not calls
    api.state.status=200;api.state.response=example('TaskItemContext')
    api.state.response['image']['height']=641
    mismatched=api.client.get(PREFIX+f'/task-items/{ITEM}/context',headers={'test-user':'3'})
    assert mismatched.status_code==502 and not calls
    api.state.response=example('TaskItemContext')
    ready=api.client.get(PREFIX+f'/task-items/{ITEM}/context',headers={'test-user':'3'})
    assert ready.status_code==503 and len(calls)==1


def test_request_json_syntax_and_payload_limits_follow_contract(api,monkeypatch):
    invalid=api.client.post(PREFIX+'/tasks',content=b'{bad',headers={'content-type':'application/json'})
    assert invalid.status_code==400 and invalid.json()['error']['code']=='invalid_json'
    monkeypatch.setattr('app.annotation_gateway.router.MAX_JSON',5)
    oversized=api.client.post(PREFIX+'/tasks',json=api.body)
    assert oversized.status_code==413 and oversized.json()['error']['code']=='payload_too_large'
    assert not api.received


@pytest.mark.parametrize('malformed', ['surrogate_value','surrogate_key','nul_value','nul_key','deep_nesting'])
def test_malformed_unicode_or_depth_input_never_prepares_or_calls_go(api,malformed):
    body=dict(api.body)
    if malformed=='surrogate_value':
        body['title']='\ud800'
    elif malformed=='surrogate_key':
        body['\udfff']='private-value'
    elif malformed=='nul_value':
        body['title']='\x00'
    elif malformed=='nul_key':
        body['\x00']='private-value'
    raw=b'['*1200+b'0'+b']'*1200 if malformed=='deep_nesting' else json.dumps(body).encode('ascii')
    response=api.client.post(PREFIX+'/tasks',content=raw,headers={'Content-Type':'application/json'})
    assert response.status_code==400 and response.json()['error']['code']=='invalid_json'
    assert not api.received
    async def inspect(session):
        assert await session.scalar(select(GatewayOperation)) is None
        assert await session.scalar(select(GatewayOperationImage)) is None
    mutate_db(api,inspect)


@pytest.mark.parametrize('malformed', ['surrogate_value','surrogate_key','nul_value','nul_key','deep_nesting'])
def test_malformed_unicode_or_depth_upstream_is_safe_502(api,malformed):
    payload=example('TaskPage')
    if malformed=='surrogate_value':
        payload['items'][0]['title']='\ud800'
    elif malformed=='surrogate_key':
        payload['\udfff']='private-value'
    elif malformed=='nul_value':
        payload['items'][0]['title']='\x00'
    elif malformed=='nul_key':
        payload['\x00']='private-value'
    api.state.raw=b'['*1200+b'0'+b']'*1200 if malformed=='deep_nesting' else json.dumps(payload).encode('ascii')
    api.state.status=200
    response=api.client.get(PREFIX+'/tasks')
    assert response.status_code==502 and response.json()['error']['code']=='upstream_invalid_response'
    assert 'private-value' not in response.text
    assert len(api.received)==1


@pytest.mark.parametrize('raw', [b'"\\ud800"',b'{"\\udfff":0}',b'"\\u0000"',b'{"\\u0000":0}',b'['*1200+b'0'+b']'*1200])
def test_json_decoder_converts_surrogates_and_parser_recursion_to_value_error(raw):
    with pytest.raises(ValueError) as caught:
        decode_json(raw)
    assert type(caught.value) is ValueError


def test_json_decoder_preserves_valid_surrogate_pairs():
    assert decode_json(b'{"emoji":"\\ud83d\\ude00"}')=={'emoji':'\U0001f600'}
