"""S1 core permissions, immutable snapshots, file retention and local seed."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

# No developer .env or cloud credentials may be consulted by these tests.
os.environ['HORSE_ENV_FILE'] = '/dev/null'
os.environ['DATABASE_URL']='sqlite+aiosqlite:///:memory:'
os.environ['SECRET_KEY']='s1-unit-test-key-which-is-at-least-32-characters'
os.environ['S3_ACCESS_KEY_ID']='test'
os.environ['S3_SECRET_ACCESS_KEY']='test'

import boto3
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api_errors import install_api_errors
from app.config import settings
from app.database import Base, get_session
from app.files.models import StoredFile
from app.files.router import router as files_router
from app.files.storage import S3Storage, get_storage
from app.projects.router import router as projects_router
from app.projects.models import Project, ProjectMember, LabelSchemaVersion
from app.projects.access import get_project_access, validate_task_assignees, get_label_schema
from app.assets.router import router as assets_router
from app.assets.models import Asset, ImageVersion
from app.assets.service import resolve_image_contexts, get_editor_image
from app.users.models import Users, UserRole
from app.users.auth import create_access_token
from scripts.seed_s1 import seed, check_local


@pytest.fixture
def core(tmp_path,monkeypatch):
    import app.dao.base as dao
    import app.files.router as files
    engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/s1.db')
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(dao,'async_session_maker',sessions)
    monkeypatch.setattr(files,'async_session_maker',sessions)
    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            session.add_all([Users(id=i,email=f'user{i}@test.org',hashed_password='unused',full_name=f'User {i}',role=role,is_active=True)
                for i,role in [(1,UserRole.ADMIN),(2,UserRole.MANAGER),(3,UserRole.ANNOTATOR),(4,UserRole.REVIEWER),(5,UserRole.MANAGER)]])
            await session.commit()
    asyncio.run(setup())
    async def session_dependency():
        async with sessions() as session:
            yield session
    app=FastAPI()
    install_api_errors(app)
    for router in [projects_router,assets_router,files_router]: app.include_router(router)
    app.dependency_overrides[get_session]=session_dependency
    with mock_aws():
        client=boto3.client('s3',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')
        client.create_bucket(Bucket='s1-core')
        storage=S3Storage(client,'s1-core')
        app.dependency_overrides[get_storage]=lambda:storage
        with TestClient(app) as api:
            def auth(user_id=1): return {'Authorization':'Bearer '+create_access_token({'sub':str(user_id),'role':'admin'})}
            yield SimpleNamespace(api=api,sessions=sessions,storage=storage,s3=client,auth=auth,engine=engine)
    asyncio.run(engine.dispose())


def project(core):
    response=core.api.post('/api/projects',headers=core.auth(),json={'name':'Horses','manager_id':2})
    assert response.status_code==201,response.text
    return response.json()['id']


def member(core,pid,uid,role):
    response=core.api.put(f'/api/projects/{pid}/members/{uid}',headers=core.auth(2),json={'role':role})
    assert response.status_code==200,response.text


def test_project_rights_isolation_revocation_and_archive(core):
    assert core.api.post('/api/projects',headers=core.auth(2),json={'name':'x','manager_id':2}).status_code==403
    pid=project(core)
    member(core,pid,3,'annotator')
    assert core.api.get(f'/api/projects/{pid}',headers=core.auth(5)).status_code==404
    assert core.api.get('/api/projects',headers=core.auth(3)).json()[0]['id']==pid
    # Forged global role/header cannot elevate the project membership.
    assert core.api.put(f'/api/projects/{pid}/members/5',headers={**core.auth(3),'X-Actor-Role':'admin'},json={'role':'manager'}).status_code==403
    assert core.api.patch(f'/api/projects/{pid}',headers=core.auth(2),json={'state':'archived'}).status_code==200
    assert core.api.get(f'/api/projects/{pid}',headers=core.auth(3)).status_code==200
    assert core.api.put(f'/api/projects/{pid}/members/5',headers=core.auth(2),json={'role':'manager'}).status_code==409
    assert core.api.patch(f'/api/projects/{pid}',headers=core.auth(2),json={'state':'active'}).status_code==200
    assert core.api.delete(f'/api/projects/{pid}/members/3',headers=core.auth(2)).status_code==204
    assert core.api.get(f'/api/projects/{pid}',headers=core.auth(3)).status_code==404
    async def inactive():
        async with core.sessions() as session:
            user=await session.get(Users,2);user.is_active=False;await session.commit()
    asyncio.run(inactive())
    assert core.api.get(f'/api/projects/{pid}',headers=core.auth(2)).status_code==404


def test_api_error_trace_bad_jwt_and_schema_snapshot(core):
    bad=core.api.get('/api/projects',headers=core.auth('not-a-number'))
    assert bad.status_code==401
    assert bad.json()['error']['request_id']==bad.headers['x-request-id']
    validation=core.api.get('/api/projects/not-a-uuid',headers=core.auth())
    assert validation.status_code==422 and validation.json()['error']['code']=='validation_error'
    pid=project(core)
    schema=core.api.get(f'/api/projects/{pid}/label-schema-versions',headers=core.auth(2)).json()[0]
    new=core.api.post(f'/api/projects/{pid}/label-schema-versions',headers=core.auth(2),json={'labels':[{'id':'horse','name':'Horse'}]})
    assert new.status_code==201 and new.json()['version']==2
    old=core.api.get(f'/api/projects/{pid}/label-schema-versions',headers=core.auth(2)).json()
    assert old[0]==schema and old[1]['labels'][0]['name']=='Horse'
    assert core.api.post(f'/api/projects/{pid}/label-schema-versions',headers=core.auth(2),json={'labels':[{'id':'x','name':'x'},{'id':'x','name':'y'}]}).status_code==422


def test_assignees_require_effective_project_roles(core):
    pid=project(core);member(core,pid,3,'annotator');member(core,pid,4,'reviewer')
    async def check():
        async with core.sessions() as session:
            await validate_task_assignees(session,pid,3,4)
            for assignee,reviewer in [(3,3),(4,3),(1,4),(5,4)]:
                with pytest.raises(HTTPException): await validate_task_assignees(session,pid,assignee,reviewer)
            member_row=await session.get(ProjectMember,(pid,3));member_row.is_active=False
            await session.flush()
            with pytest.raises(HTTPException): await validate_task_assignees(session,pid,3,4)
    asyncio.run(check())


def test_file_guard_and_private_working_context(core):
    pid=project(core)
    source_id,work_id,asset_id,image_id=[str(uuid4()) for _ in range(4)]
    digest=hashlib.sha256(b'png').hexdigest()
    async def setup():
        async with core.sessions() as session:
            for file_id,key in [(source_id,'originals/test'),(work_id,'working/test')]:
                session.add(StoredFile(id=file_id,owner_id=2,original_name='image.png',size=3,content_type='image/png',staging_key='staging/'+file_id,object_key=key,status='ready',sha256=digest))
            await session.flush()
            session.add(Asset(id=asset_id,project_id=pid,source_file_id=source_id,source_uri='fixture:x',license='synthetic',session_id=str(uuid4())))
            await session.flush()
            session.add(ImageVersion(id=image_id,project_id=pid,asset_id=asset_id,file_id=work_id,sha256=digest,width=640,height=640,status='ready'))
            await session.commit()
        async with core.sessions() as session:
            image=await get_editor_image(session,pid,image_id)
            assert image['object_key']=='working/test' and image['content_type']=='image/png'
            assert set((await resolve_image_contexts(session,pid,[image_id]))[0])=={'image_version_id','sha256','width','height'}
            with pytest.raises(HTTPException): await resolve_image_contexts(session,str(uuid4()),[image_id])
            with pytest.raises(HTTPException): await resolve_image_contexts(session,pid,[image_id,image_id])
    asyncio.run(setup())
    for key in ['originals/test','working/test']: core.s3.put_object(Bucket='s1-core',Key=key,Body=b'png')
    for file_id in [source_id,work_id]:
        assert core.api.delete(f'/files/{file_id}',headers=core.auth(2)).status_code==409
    assert core.s3.list_objects_v2(Bucket='s1-core')['KeyCount']==2
    assert 'response-content-disposition=inline' in core.storage.view_url('working/test','image/png')
    assert core.api.get(f'/api/projects/{pid}/image-versions/{image_id}',headers=core.auth(3)).status_code==404


def test_seed_guard_idempotency_and_conflicts(tmp_path,monkeypatch):
    engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/seed-s1.db')
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(settings,'S1_DEV_MODE',True)
    monkeypatch.setattr(settings,'DATABASE_URL',f'sqlite+aiosqlite:///{tmp_path}/seed-s1.db')
    monkeypatch.setattr(settings,'S3_ENDPOINT_URL','http://localhost:19000')
    async def setup():
        async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    asyncio.run(setup())
    with mock_aws():
        client=boto3.client('s3',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test');client.create_bucket(Bucket='s1-seed')
        storage=S3Storage(client,'s1-seed')
        first=asyncio.run(seed(settings,sessions,storage))
        async def password():
            async with sessions() as session: return (await session.get(Users,1)).hashed_password
        old_password=asyncio.run(password())
        monkeypatch.setattr(settings,'S1_SEED_PASSWORD','new-password-not-applied')
        assert asyncio.run(seed(settings,sessions,storage))==first
        assert asyncio.run(password())==old_password
        assert client.list_objects_v2(Bucket='s1-seed')['KeyCount']==6
        async def conflict():
            async with sessions() as session:
                project=await session.get(Project,first['project_id']);project.name='modified';await session.commit()
        asyncio.run(conflict())
        with pytest.raises(RuntimeError,match='conflict'): asyncio.run(seed(settings,sessions,storage))
        monkeypatch.setattr(settings,'S3_ENDPOINT_URL','https://storage.yandexcloud.net')
        with pytest.raises(RuntimeError,match='local HTTP'): asyncio.run(seed(settings,sessions,storage))
    asyncio.run(engine.dispose())


def test_distinct_project_roles_ignore_global_role(core):
    first=project(core);second=project(core)
    member(core,first,3,'annotator');member(core,second,3,'reviewer')
    member(core,first,5,'annotator')  # Global manager is only an annotator here.
    async def check():
        async with core.sessions() as session:
            user=await session.get(Users,3)
            assert (await get_project_access(session,first,user)).role=='annotator'
            assert (await get_project_access(session,second,user)).role=='reviewer'
            manager=await session.get(Users,5)
            assert (await get_project_access(session,first,manager)).role=='annotator'
            with pytest.raises(HTTPException): await get_project_access(session,second,manager)
    asyncio.run(check())


def test_alembic_upgrade_preserves_legacy_files_and_ids(tmp_path):
    import sqlite3
    import subprocess
    import sys
    path=tmp_path/'s1-legacy.db'
    env={**os.environ,'HORSE_ENV_FILE':'/dev/null','DATABASE_URL':f'sqlite+aiosqlite:///{path}',
         'MIGRATION_DATABASE_URL':'','PYTHONDONTWRITEBYTECODE':'1'}
    root=Path(__file__).resolve().parents[1]
    def upgrade(revision):
        result=subprocess.run([sys.executable,'-m','alembic','upgrade',revision],cwd=root,env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    upgrade('20261105_files')
    file_id=str(uuid4())
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users (id,email,hashed_password,full_name,role,is_active,created_at) VALUES (9,'legacy@test.org','hash','Legacy','ADMIN',1,'2026-01-01')")
        db.execute("INSERT INTO files (id,owner_id,original_name,size,content_type,staging_key,object_key,status,sha256,created_at) VALUES (?,9,'horse.png',3,'image/png','old-staging','originals/unchanged','ready',?,'2026-01-01')",(file_id,'a'*64))
    upgrade('head')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id,email FROM users').fetchall()==[(9,'legacy@test.org')]
        assert db.execute('SELECT id,owner_id,object_key,sha256 FROM files').fetchall()==[(file_id,9,'originals/unchanged','a'*64)]
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()==('20261108_gateway',)


def test_lifespan_requires_migrations_and_closes_client(tmp_path,monkeypatch):
    import importlib
    from sqlalchemy import text
    main=importlib.import_module('app.main')
    engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/s1-lifespan.db')
    monkeypatch.setattr(main,'engine',engine)
    monkeypatch.setattr(settings,'AUTO_CREATE_TABLES',False)
    monkeypatch.setattr(settings,'ENFORCE_MIGRATIONS',True)
    class Client:
        closed=False
        async def aclose(self): self.closed=True
    client=Client()
    monkeypatch.setattr(main.AnnotationClient,'from_settings',lambda settings:client)
    async def check():
        with pytest.raises(RuntimeError,match='migrations are missing'):
            async with main.lifespan(main.app): pass
        async with engine.begin() as conn:
            await conn.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
            await conn.execute(text("INSERT INTO alembic_version VALUES ('20261108_gateway')"))
        async with main.lifespan(main.app):
            assert main.app.state.annotation_client is client
            assert not client.closed
        assert client.closed
    asyncio.run(check())


@pytest.mark.parametrize('content_type',[None,'text/plain','application/json'])
@pytest.mark.parametrize('cookie_name',['access_token','booking_access_token'])
def test_cookie_api_csrf_rejects_attacker_simple_json(core,content_type,cookie_name):
    token=core.auth()['Authorization'].removeprefix('Bearer ')
    core.api.cookies.set(cookie_name,token)
    headers={'Origin':'http://attacker.example',**core.auth()}
    if content_type is not None: headers['Content-Type']=content_type
    response=core.api.post('/api/projects',headers=headers,content=json.dumps({'name':'CSRF','manager_id':2}))
    assert response.status_code==403
    assert response.json()['error']['code']=='forbidden'
    assert response.json()['error']['details']=={}
    assert response.json()['error']['request_id']==response.headers['x-request-id']
    assert core.api.get('/api/projects',headers=core.auth()).json()==[]


def test_cookie_api_csrf_allowed_origins_and_bearer_only(core,monkeypatch):
    monkeypatch.setattr(settings,'API_CORS_ORIGINS',['http://localhost:3000'])
    payload={'name':'Allowed','manager_id':2}
    # Command-line Bearer clients need no Origin when no cookie is selected.
    assert core.api.post('/api/projects',headers=core.auth(),json=payload).status_code==201
    core.api.cookies.set('access_token',core.auth()['Authorization'].removeprefix('Bearer '))
    assert core.api.post('/api/projects',headers=core.auth(),json=payload).status_code==403
    for origin in ['http://testserver','http://localhost:3000']:
        assert core.api.post('/api/projects',headers={'Origin':origin},json=payload).status_code==201
    # Exact origins only: prefixes, additional origins and null are rejected.
    for origin in ['http://localhost:3000.attacker.example','http://testserver.attacker.example','null','http://localhost:3000/']:
        assert core.api.post('/api/projects',headers={'Origin':origin},json=payload).status_code==403
    # Compatibility: the new /api guard does not change legacy files behavior.
    assert core.api.post('/files/uploads',headers={'Origin':'http://attacker.example'},json={
        'original_name':'test.png','size':3,'content_type':'image/png'}).status_code==201


def test_error_contract_details_and_validation_privacy(core):
    from app.annotation_gateway.validation import validate
    from app.api_errors import api_error, json_pointer
    unauthorized=core.api.get('/api/projects')
    assert unauthorized.status_code==401
    assert unauthorized.json()['error']['code']=='unauthenticated'
    assert unauthorized.json()['error']['details']=={}
    validate(unauthorized.json(),{'$ref':'#/components/schemas/Error'})
    response=core.api.post('/api/projects',headers=core.auth(),json={
        'name':'x','manager_id':'never-echo-this-secret','a/b~':'another-private-value'})
    assert response.status_code==422
    validate(response.json(),{'$ref':'#/components/schemas/Error'})
    violations=response.json()['error']['details']['violations']
    assert {entry['path'] for entry in violations}=={'/manager_id','/a~1b~0'}
    assert all(set(entry)=={'path','message'} for entry in violations)
    assert 'never-echo-this-secret' not in response.text and 'another-private-value' not in response.text
    assert json_pointer(('body','boxes',0,'xmin'))=='/boxes/0/xmin'
    factory=api_error(422,'validation_error','Invalid JSON request')
    assert factory.detail['details']=={'violations':[{'path':'/','message':'Invalid JSON request'}]}
    validate({'error':{**factory.detail,'request_id':str(uuid4())}},{'$ref':'#/components/schemas/Error'})


@pytest.mark.parametrize('invalid_character',['\x00','\ud800'])
def test_core_rejects_database_invalid_text_without_mutations(core,invalid_character):
    bad='private-invalid-text'+invalid_character
    def send(method,path,payload,expected_path,user=1):
        # Escaped JSON sends lone surrogates over the wire without requiring the
        # HTTP client itself to encode an invalid Python string as UTF-8.
        response=core.api.request(method,path,headers={**core.auth(user),'Content-Type':'application/json'},
                                  content=json.dumps(payload))
        assert response.status_code==422,response.text
        violations=response.json()['error']['details']['violations']
        assert any(v['path']==expected_path for v in violations)
        assert all(set(v)=={'path','message'} for v in violations)
        assert 'private-invalid-text' not in response.text
    send('POST','/api/projects',{'name':bad,'manager_id':2},'/name')
    assert core.api.get('/api/projects',headers=core.auth()).json()==[]
    pid=project(core)
    send('PATCH',f'/api/projects/{pid}',{'name':bad},'/name',user=2)
    assert core.api.get(f'/api/projects/{pid}',headers=core.auth(2)).json()['name']=='Horses'
    file_id=str(uuid4())
    async def ready_file():
        async with core.sessions() as session,session.begin():
            session.add(StoredFile(id=file_id,owner_id=2,original_name='fixture.png',size=3,content_type='image/png',
                                   staging_key='staging/'+file_id,object_key='originals/'+file_id,status='ready',sha256='a'*64))
    asyncio.run(ready_file())
    asset={'source_file_id':file_id,'source_uri':'fixture:image','license':'synthetic','session_id':str(uuid4())}
    for field in ['source_uri','license']:
        send('POST',f'/api/projects/{pid}/assets',{**asset,field:bad},'/'+field,user=2)
        assert core.api.get(f'/api/projects/{pid}/assets',headers=core.auth(2)).json()==[]
    send('POST',f'/api/projects/{pid}/label-schema-versions',{'labels':[{'id':'horse','name':bad}]},'/labels/0/name',user=2)
    schemas=core.api.get(f'/api/projects/{pid}/label-schema-versions',headers=core.auth(2)).json()
    assert len(schemas)==1 and schemas[0]['version']==1
