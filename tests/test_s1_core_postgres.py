"""Opt-in real PostgreSQL S1 locking and projection checks.

S1_TEST_CORE_DATABASE_URL must identify the disposable local horse_s1_test_core
DB. The fixture resets its public schema and applies Alembic before the checks.
"""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

URL = os.environ.get('S1_TEST_CORE_DATABASE_URL')
os.environ['HORSE_ENV_FILE'] = '/dev/null'
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
os.environ['SECRET_KEY'] = 's1-pg-test-key-with-at-least-32-characters'
os.environ['S3_ACCESS_KEY_ID'] = 'test'
os.environ['S3_SECRET_ACCESS_KEY'] = 'test'
pytestmark = pytest.mark.skipif(not URL, reason='Set S1_TEST_CORE_DATABASE_URL for isolated PostgreSQL checks')


@pytest.fixture(scope='module')
def pg_url():
    parsed = urlsplit(URL)
    if parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.path != '/horse_s1_test_core':
        pytest.fail('PostgreSQL core tests require the disposable local horse_s1_test_core database')
    env = {**os.environ, 'HORSE_ENV_FILE': '/dev/null', 'DATABASE_URL': URL,
           'MIGRATION_DATABASE_URL': URL, 'SECRET_KEY': 's1-pg-test-key-with-at-least-32-characters',
           'S3_ACCESS_KEY_ID': 'test', 'S3_SECRET_ACCESS_KEY': 'test', 'PYTHONDONTWRITEBYTECODE': '1'}
    root = Path(__file__).resolve().parents[1]
    async def reset():
        import asyncpg
        connection = await asyncpg.connect(URL.replace('postgresql+asyncpg://', 'postgresql://', 1))
        try:
            await connection.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
        finally:
            await connection.close()
    asyncio.run(reset())
    for command in [('upgrade', 'head'), ('downgrade', 'base'), ('upgrade', 'head')]:
        result = subprocess.run([sys.executable, '-m', 'alembic', *command], cwd=root,
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    return URL


def dependencies(url):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    return create_async_engine(url), async_sessionmaker


async def context(sessions):
    from app.users.models import Users, UserRole
    from app.projects.models import Project, ProjectMember, LabelSchemaVersion
    from app.files.models import StoredFile
    token = uuid4().hex
    async with sessions() as session, session.begin():
        user = Users(email=f'{token}@pg.test', hashed_password='unused', full_name='PG manager', role=UserRole.ANNOTATOR, is_active=True)
        session.add(user)
        project = Project(id=str(uuid4()), name='PG locking', state='active', label_schema_id=str(uuid4()), current_label_schema_version=1)
        session.add(project)
        await session.flush()
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role='manager', is_active=True))
        session.add(LabelSchemaVersion(schema_id=project.label_schema_id, version=1, project_id=project.id, labels=[{'id':'horse','name':'Лошадь'}]))
        file = StoredFile(id=str(uuid4()), owner_id=user.id, original_name='pg.png', size=3,
                          content_type='image/png', staging_key=f'staging/{token}', object_key=f'originals/{token}',
                          status='ready', sha256='a'*64)
        session.add(file)
    return SimpleNamespace(user=user, project=project, file=file)


def test_postgres_migration_membership_and_schema_projection(pg_url):
    async def run():
        from sqlalchemy import text
        from fastapi import HTTPException
        from app.projects.access import get_project_access, get_label_schema
        from app.projects.models import ProjectMember, LabelSchemaVersion
        from app.assets.models import Asset, ImageVersion
        from app.files.models import StoredFile
        from app.assets.service import resolve_image_contexts
        engine, maker = dependencies(pg_url)
        sessions = maker(engine, expire_on_commit=False)
        try:
            data = await context(sessions)
            async with sessions() as session, session.begin():
                assert (await session.execute(text('SELECT version_num FROM alembic_version'))).scalar_one() == '20261108_gateway'
                assert (await get_project_access(session, data.project.id, data.user)).role == 'manager'
                session.add(LabelSchemaVersion(schema_id=data.project.label_schema_id, version=2, project_id=data.project.id, labels=[{'id':'horse','name':'Horse'}]))
                await session.flush()
                assert (await get_label_schema(session, data.project.id, 1))['labels'][0]['name'] == 'Лошадь'
                assert (await get_label_schema(session, data.project.id, 2))['labels'][0]['name'] == 'Horse'
                asset = Asset(id=str(uuid4()), project_id=data.project.id, source_file_id=data.file.id,
                              source_uri='fixture:pg', license='synthetic', session_id=str(uuid4()))
                session.add(asset)
                await session.flush()
                working = StoredFile(id=str(uuid4()), owner_id=data.user.id, original_name='working.png', size=3,
                                     content_type='image/png', staging_key='staging/'+uuid4().hex, object_key='working/'+uuid4().hex,
                                     status='ready', sha256='a'*64)
                session.add(working)
                await session.flush()
                image = ImageVersion(id=str(uuid4()), project_id=data.project.id, asset_id=asset.id,
                                     file_id=working.id, sha256='a'*64, width=640, height=640, status='ready')
                session.add(image)
                await session.flush()
                assert await resolve_image_contexts(session, data.project.id, [image.id], lock=True) == [
                    {'image_version_id':image.id,'sha256':'a'*64,'width':640,'height':640}]
                with pytest.raises(HTTPException):
                    await resolve_image_contexts(session, str(uuid4()), [image.id])
                member = await session.get(ProjectMember, (data.project.id, data.user.id))
                member.is_active = False
                await session.flush()
                with pytest.raises(HTTPException) as denied:
                    await get_project_access(session, data.project.id, data.user)
                assert denied.value.status_code == 404
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_postgres_attachment_lock_blocks_delete_before_s3(pg_url):
    async def run():
        from fastapi import HTTPException
        from sqlalchemy import text
        from app.assets.models import Asset
        from app.assets.service import lock_ready_file
        from app.files.router import delete_file
        engine, maker = dependencies(pg_url)
        sessions = maker(engine, expire_on_commit=False)
        storage = SimpleNamespace(calls=[])
        storage.delete = lambda *keys: storage.calls.append(keys)
        try:
            data = await context(sessions)
            pid_ready = asyncio.Future()
            async def delete():
                async with sessions() as session:
                    pid_ready.set_result((await session.execute(text('SELECT pg_backend_pid()'))).scalar_one())
                    with pytest.raises(HTTPException) as denied:
                        await delete_file(data.file.id, data.user, session, storage)
                    assert denied.value.status_code == 409
            async with sessions() as attach, attach.begin():
                await lock_ready_file(attach, data.file.id, data.user)
                attach.add(Asset(id=str(uuid4()),project_id=data.project.id,source_file_id=data.file.id,
                                 source_uri='fixture:race',license='synthetic',session_id=str(uuid4())))
                await attach.flush()
                task = asyncio.create_task(delete())
                pid = await pid_ready
                # Observe a real PostgreSQL lock wait, rather than relying on timing.
                async with sessions() as observer:
                    for _ in range(100):
                        wait = (await observer.execute(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid'), {'pid':pid})).scalar_one()
                        await observer.rollback()
                        if wait == 'Lock':
                            break
                        await asyncio.sleep(.02)
                    else:
                        pytest.fail('DELETE did not wait for the attachment row lock')
                assert not task.done() and storage.calls == []
            await asyncio.wait_for(task, 5)
            assert storage.calls == []
        finally:
            await engine.dispose()
    asyncio.run(run())
