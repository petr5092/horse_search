"""Idempotent local-only seed; requires an already migrated database."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid5

FIXTURES = Path(__file__).resolve().parents[1] / 'docs/contracts/fixtures/annotation-v0.1'


def check_local(settings):
    if not settings.S1_DEV_MODE:
        raise RuntimeError('S1_DEV_MODE must be enabled')
    endpoint = urlsplit(settings.S3_ENDPOINT_URL or '')
    db = urlsplit(settings.DATABASE_URL or '')
    # Explicit allow-list: never infer safety from test-looking credentials.
    if endpoint.scheme != 'http' or endpoint.hostname not in {'localhost','127.0.0.1','::1','minio','s1-minio'}:
        raise RuntimeError('Seed requires an explicit local HTTP S3 endpoint')
    if db.scheme.startswith('postgresql'):
        if db.hostname not in {'localhost','127.0.0.1','::1','postgres','s1-postgres'} or not db.path.endswith('/horse_s1'):
            raise RuntimeError('Seed requires local horse_s1 PostgreSQL database')
    elif not db.scheme.startswith('sqlite') or ':memory:' not in db.path and 's1' not in db.path:
        raise RuntimeError('Seed requires explicitly named s1 test database')


async def seed(settings, sessions, storage):
    check_local(settings)
    from sqlalchemy import select, text
    from app.users.models import Users, UserRole
    from app.users.auth import get_password_hash
    from app.projects.models import Project, ProjectMember, LabelSchemaVersion
    from app.assets.models import Asset, ImageVersion
    from app.assets.service import lock_ready_file
    from app.files.models import StoredFile
    manifest = json.loads((FIXTURES/'manifest.json').read_text())
    schema = json.loads((FIXTURES/'create-task.json').read_text())['label_schema']
    project_id = manifest['project_id']
    namespace = UUID(project_id)
    def identifier(name):
        return str(uuid5(namespace,name))
    # Build and validate every local byte before database/S3 work.
    frames = []
    for frame in manifest['frames']:
        body = (FIXTURES/frame['file']).read_bytes()
        if hashlib.sha256(body).hexdigest() != frame['image']['sha256']:
            raise RuntimeError('Fixture digest mismatch')
        frames.append((frame,body))
    async with sessions() as session, session.begin():
        if session.bind.dialect.name == 'postgresql':
            await session.execute(text('SELECT pg_advisory_xact_lock(20261108)'))
        # All conflicts are checked before any writes or S3 calls.
        planned = []
        def expect(existing, values):
            if existing is not None and any(getattr(existing,k) != v for k,v in values.items()):
                raise RuntimeError('Seed IDs conflict with existing data; refusing to overwrite')
        for role,user_id in manifest['actors'].items():
            values = dict(email=f'{role}@s1.example',full_name=f'S1 {role}',role=UserRole(role),is_active=True)
            current = await session.get(Users,user_id)
            expect(current,values)
            if current is None:
                planned.append(Users(id=user_id,hashed_password=get_password_hash(settings.S1_SEED_PASSWORD),**values))
        values = dict(name='S1 synthetic project',state='active',label_schema_id=schema['id'],current_label_schema_version=1)
        current = await session.get(Project,project_id); expect(current,values)
        if current is None: planned.append(Project(id=project_id,**values))
        for role in ['manager','annotator','reviewer']:
            values = dict(role=role,is_active=True)
            current = await session.get(ProjectMember,(project_id,manifest['actors'][role])); expect(current,values)
            if current is None: planned.append(ProjectMember(project_id=project_id,user_id=manifest['actors'][role],**values))
        values = dict(project_id=project_id,labels=schema['labels'])
        current = await session.get(LabelSchemaVersion,(schema['id'],schema['version'])); expect(current,values)
        if current is None: planned.append(LabelSchemaVersion(schema_id=schema['id'],version=schema['version'],**values))
        uploads = []
        for frame,body in frames:
            source_id,working_id,asset_id = [identifier(prefix+frame['file']) for prefix in ['source:','working:','asset:']]
            for file_id,prefix in [(source_id,'originals'),(working_id,'working')]:
                key = f'{prefix}/s1/{file_id}.png'
                values = dict(owner_id=1,original_name=frame['file'],size=len(body),content_type='image/png',
                    staging_key=f'staging/s1/{file_id}',object_key=key,status='ready',sha256=frame['image']['sha256'],expected_sha256=frame['image']['sha256'])
                current = await session.get(StoredFile,file_id); expect(current,values)
                if current is None:
                    planned.append(StoredFile(id=file_id,**values))
                uploads.append((key,body,frame['image']['sha256']))
            values = dict(project_id=project_id,source_file_id=source_id,source_uri=f'fixture:{frame["file"]}',license='synthetic-test-only',session_id=frame['session_id'])
            current = await session.get(Asset,asset_id); expect(current,values)
            if current is None: planned.append(Asset(id=asset_id,**values))
            values = dict(project_id=project_id,asset_id=asset_id,file_id=working_id,sha256=frame['image']['sha256'],width=frame['image']['width'],height=frame['image']['height'],status='ready')
            current = await session.get(ImageVersion,frame['image']['image_version_id']); expect(current,values)
            if current is None: planned.append(ImageVersion(id=frame['image']['image_version_id'],**values))
        from botocore.exceptions import ClientError
        for key,body,digest in uploads:
            try:
                result = await asyncio.to_thread(storage.client.get_object,Bucket=storage.bucket,Key=key)
                current_body = await asyncio.to_thread(result['Body'].read)
                result['Body'].close()
                if hashlib.sha256(current_body).hexdigest() != digest or result.get('ContentType') != 'image/png':
                    raise RuntimeError('Existing S3 seed object differs; refusing to overwrite')
            except ClientError as exc:
                if exc.response.get('Error',{}).get('Code') not in {'404','NoSuchKey','NotFound'}:
                    raise
                await asyncio.to_thread(storage.client.put_object,Bucket=storage.bucket,Key=key,Body=body,ContentType='image/png',IfNoneMatch='*')
        # Explicit dependency order and file locks shared with DELETE.
        for model in [Users,Project,StoredFile,ProjectMember,LabelSchemaVersion,Asset,ImageVersion]:
            if model in {Asset,ImageVersion}:
                for obj in planned:
                    if isinstance(obj,model):
                        await lock_ready_file(session,obj.source_file_id if model is Asset else obj.file_id)
            session.add_all([obj for obj in planned if isinstance(obj,model)])
            await session.flush()
        if session.bind.dialect.name == 'postgresql':
            await session.execute(text("SELECT setval(pg_get_serial_sequence('users','id'), GREATEST((SELECT max(id) FROM users), 4), true)"))
    return {'project_id':project_id,'image_version_ids':[frame['image']['image_version_id'] for frame,_ in frames]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',required=True)
    parser.parse_args()
    # Never discover production .env by default.
    os.environ.setdefault('HORSE_ENV_FILE','/dev/null')
    from app.config import settings
    check_local(settings)
    from app.database import async_session_maker
    from app.files.storage import get_storage
    print(json.dumps(asyncio.run(seed(settings,async_session_maker,get_storage()))))


if __name__ == '__main__':
    main()
