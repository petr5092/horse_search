"""Isolated API + real boto3 serialization against an in-memory S3 emulator."""
import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber
from fastapi import FastAPI
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.files.models import StoredFile
from app.files.router import router
from app.files.storage import S3Storage, get_storage
from app.users.auth import create_access_token
from app.users.models import Users, UserRole


@pytest.fixture
def api(tmp_path, monkeypatch):
    import app.dao.base as dao
    import app.files.router as files_router
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dao, "async_session_maker", sessions)
    monkeypatch.setattr(files_router, "async_session_maker", sessions)
    monkeypatch.setattr(settings, "S3_MAX_FILE_SIZE", 20)

    async def setup():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            for i, role in [(1, UserRole.ANNOTATOR), (2, UserRole.ANNOTATOR), (3, UserRole.ADMIN)]:
                session.add(Users(id=i, email=f"user{i}@example.org", hashed_password="unused",
                                  full_name=f"User {i}", role=role, is_active=True))
            await session.commit()
    asyncio.run(setup())
    app = FastAPI()
    app.include_router(router)
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
        s3.create_bucket(Bucket="test-files")
        storage = S3Storage(s3, "test-files")
        app.dependency_overrides[get_storage] = lambda: storage
        with TestClient(app) as client:
            def auth(user_id=1):
                return {"Authorization": "Bearer " + create_access_token({"sub": str(user_id)})}
            yield SimpleNamespace(client=client, s3=s3, storage=storage, auth=auth, sessions=sessions)
    asyncio.run(engine.dispose())


def request_upload(api, **extra):
    response = api.client.post("/files/uploads", headers=api.auth(), json={
        "original_name": "лошадь.jpg", "size": 5, "content_type": "image/jpeg", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def put_upload(api, upload, body=b"horse", content_type="image/jpeg"):
    api.s3.put_object(Bucket="test-files", Key=upload["fields"]["key"], Body=body, ContentType=content_type)


def test_authenticated_lifecycle_and_replay(api):
    upload = request_upload(api, sha256=hashlib.sha256(b"horse").hexdigest())
    file_id = upload["file"]["id"]
    assert upload["file"]["status"] == "pending"
    policy = json.loads(base64.b64decode(upload["fields"]["policy"]))
    assert ["content-length-range", 5, 5] in policy["conditions"]
    assert {"Content-Type": "image/jpeg"} in policy["conditions"]
    assert api.client.get(f"/files/{file_id}/download", headers=api.auth()).status_code == 409
    put_upload(api, upload)
    complete = api.client.post(f"/files/{file_id}/complete", headers=api.auth())
    assert complete.status_code == 200, complete.text
    assert complete.json()["sha256"] == hashlib.sha256(b"horse").hexdigest()
    originals = api.s3.list_objects_v2(Bucket="test-files", Prefix="originals/")["Contents"]
    original_key = originals[0]["Key"]
    assert original_key != upload["fields"]["key"]
    put_upload(api, upload, b"other")  # Replaying the original POST cannot alter originals.
    assert api.client.post(f"/files/{file_id}/complete", headers=api.auth()).json() == complete.json()
    assert api.s3.get_object(Bucket="test-files", Key=original_key)["Body"].read() == b"horse"
    assert api.client.get("/files", headers=api.auth()).json()[0]["id"] == file_id
    link = api.client.get(f"/files/{file_id}/download", headers=api.auth())
    assert link.status_code == 200 and "response-content-disposition=" in link.json()["url"]
    assert "originals/" in link.json()["url"]
    assert api.client.delete(f"/files/{file_id}", headers=api.auth()).status_code == 204
    assert api.client.get("/files", headers=api.auth()).json() == []
    assert api.s3.list_objects_v2(Bucket="test-files").get("KeyCount") == 0


def test_auth_and_owner_admin_access(api):
    assert api.client.get("/files").status_code == 401
    upload = request_upload(api)
    file_id = upload["file"]["id"]
    put_upload(api, upload)
    for method, suffix in [("post", "complete"), ("get", "download"), ("delete", "")]:
        path = f"/files/{file_id}" + ("/" + suffix if suffix else "")
        assert getattr(api.client, method)(path, headers=api.auth(2)).status_code == 404
    assert api.client.get("/files", headers=api.auth(2)).json() == []
    assert api.client.get("/files", headers=api.auth(3)).json() == []
    assert api.client.post(f"/files/{file_id}/complete", headers=api.auth(3)).status_code == 200
    assert api.client.get(f"/files/{file_id}/download", headers=api.auth(3)).status_code == 200
    assert api.client.delete(f"/files/{file_id}", headers=api.auth(3)).status_code == 204


@pytest.mark.parametrize("problem", ["missing", "size", "type", "checksum"])
def test_completion_rejects_bad_upload_and_allows_retry(api, problem):
    upload = request_upload(api, **({"sha256": "0" * 64} if problem == "checksum" else {}))
    file_id = upload["file"]["id"]
    if problem != "missing":
        put_upload(api, upload, b"longer" if problem == "size" else b"horse",
                   "text/plain" if problem == "type" else "image/jpeg")
    response = api.client.post(f"/files/{file_id}/complete", headers=api.auth())
    assert response.status_code == 409, response.text
    assert api.client.get("/files", headers=api.auth()).json()[0]["status"] == "pending"
    assert not api.s3.list_objects_v2(Bucket="test-files", Prefix="originals/").get("Contents")
    if problem != "checksum":
        put_upload(api, upload)
        assert api.client.post(f"/files/{file_id}/complete", headers=api.auth()).status_code == 200


def test_size_and_name_validation(api):
    response = api.client.post("/files/uploads", headers=api.auth(), json={
        "original_name": "x.jpg", "size": 21, "content_type": "image/jpeg"})
    assert response.status_code == 413
    for field, value in [("size", 0), ("original_name", "../x"), ("content_type", "x\r\ny")]:
        response = api.client.post("/files/uploads", headers=api.auth(), json={
            "original_name": "x.jpg", "size": 5, "content_type": "image/jpeg", field: value})
        assert response.status_code == 422
    assert api.client.get("/files?limit=101", headers=api.auth()).status_code == 422


def test_storage_failure_is_retryable(api):
    upload = request_upload(api)
    file_id = upload["file"]["id"]
    with Stubber(api.s3) as stubber:
        stubber.add_client_error("head_object", service_error_code="AccessDenied", http_status_code=403)
        stubber.add_response("delete_object", {})
        response = api.client.post(f"/files/{file_id}/complete", headers=api.auth())
    assert response.status_code == 503
    assert api.client.get("/files", headers=api.auth()).json()[0]["status"] == "pending"
    put_upload(api, upload)
    assert api.client.post(f"/files/{file_id}/complete", headers=api.auth()).status_code == 200
    with Stubber(api.s3) as stubber:
        stubber.add_client_error("delete_object", service_error_code="InternalError", http_status_code=500)
        assert api.client.delete(f"/files/{file_id}", headers=api.auth()).status_code == 503
    assert api.client.get("/files", headers=api.auth()).json()[0]["status"] == "deleting"
    assert api.client.get(f"/files/{file_id}/download", headers=api.auth()).status_code == 409
    assert api.client.delete(f"/files/{file_id}", headers=api.auth()).status_code == 204


def test_presigning_failure(api, monkeypatch):
    def fail(*args):
        raise EndpointConnectionError(endpoint_url="http://storage.invalid")
    monkeypatch.setattr(api.storage, "upload_form", fail)
    response = api.client.post("/files/uploads", headers=api.auth(), json={
        "original_name": "x.jpg", "size": 5, "content_type": "image/jpeg"})
    assert response.status_code == 503
    assert api.client.get("/files", headers=api.auth()).json() == []


def test_copy_is_conditional(api, monkeypatch):
    upload = request_upload(api)
    put_upload(api, upload)
    copy = api.s3.copy_object
    calls = []
    def tracked_copy(**kwargs):
        calls.append(kwargs)
        return copy(**kwargs)
    monkeypatch.setattr(api.s3, "copy_object", tracked_copy)
    assert api.client.post(f"/files/{upload['file']['id']}/complete", headers=api.auth()).status_code == 200
    assert calls[0]["CopySourceIfMatch"] == '"' + hashlib.md5(b"horse").hexdigest() + '"'


def test_claim_excludes_other_operations(api):
    upload = request_upload(api)
    file_id = upload["file"]["id"]
    async def set_finalizing():
        async with api.sessions() as session:
            file = await session.get(StoredFile, file_id)
            file.status = "finalizing"
            await session.commit()
    asyncio.run(set_finalizing())
    assert api.client.post(f"/files/{file_id}/complete", headers=api.auth()).status_code == 409
    assert api.client.delete(f"/files/{file_id}", headers=api.auth()).status_code == 409


def test_concurrent_completion_and_delete_are_excluded(api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    upload = request_upload(api)
    put_upload(api, upload)
    file_id = upload["file"]["id"]
    entered, release = Event(), Event()
    finalize = api.storage.finalize
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return finalize(*args)
    monkeypatch.setattr(api.storage, "finalize", blocked)
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(api.client.post, f"/files/{file_id}/complete", headers=api.auth())
        try:
            assert entered.wait(5)
            assert api.client.post(f"/files/{file_id}/complete", headers=api.auth()).status_code == 409
            assert api.client.delete(f"/files/{file_id}", headers=api.auth()).status_code == 409
        finally:
            release.set()
        assert pending.result().status_code == 200


@pytest.mark.parametrize("operation", ["finalizing", "deleting"])
def test_recovers_abandoned_operation(api, operation):
    from datetime import datetime, timedelta
    upload = request_upload(api)
    put_upload(api, upload)
    file_id = upload["file"]["id"]
    abandoned_key = f"originals/1/{file_id}/abandoned"
    api.s3.put_object(Bucket="test-files", Key=abandoned_key, Body=b"horse")
    async def abandon():
        async with api.sessions() as session:
            file = await session.get(StoredFile, file_id)
            file.status = operation
            file.object_key = abandoned_key
            file.operation_started_at = datetime.utcnow() - timedelta(hours=1)
            await session.commit()
    asyncio.run(abandon())
    if operation == "finalizing":
        response = api.client.post(f"/files/{file_id}/complete", headers=api.auth())
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ready"
        assert len(api.s3.list_objects_v2(Bucket="test-files", Prefix="originals/")["Contents"]) == 1
    else:
        assert api.client.delete(f"/files/{file_id}", headers=api.auth()).status_code == 204
        assert api.client.get("/files", headers=api.auth()).json() == []
    with pytest.raises(ClientError):
        api.s3.head_object(Bucket="test-files", Key=abandoned_key)


def test_late_completion_cannot_overwrite_recovered_claim(api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta
    from threading import Event
    upload = request_upload(api)
    put_upload(api, upload)
    file_id = upload["file"]["id"]
    entered, release = Event(), Event()
    finalize = api.storage.finalize
    def blocked(file, key):
        checksum = finalize(file, key)
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return checksum
    monkeypatch.setattr(api.storage, "finalize", blocked)
    async def expire_claim():
        async with api.sessions() as session:
            file = await session.get(StoredFile, file_id)
            file.operation_started_at = datetime.utcnow() - timedelta(hours=1)
            await session.commit()
    with ThreadPoolExecutor() as executor:
        first = executor.submit(api.client.post, f"/files/{file_id}/complete", headers=api.auth())
        try:
            assert entered.wait(5)
            asyncio.run(expire_claim())
            second = api.client.post(f"/files/{file_id}/complete", headers=api.auth())
            assert second.status_code == 200, second.text
        finally:
            release.set()
        assert first.result().status_code == 409
    assert api.client.get("/files", headers=api.auth()).json()[0]["status"] == "ready"
    assert len(api.s3.list_objects_v2(Bucket="test-files", Prefix="originals/")["Contents"]) == 1


def test_client_construction_failure_is_503(monkeypatch):
    import app.files.storage as module
    def fail():
        raise EndpointConnectionError(endpoint_url="http://storage.invalid")
    monkeypatch.setattr(module, "_configured_storage", fail)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        module.get_storage()
    assert error.value.status_code == 503


def test_source_changed_during_copy_is_retryable(api):
    upload = request_upload(api)
    file_id = upload["file"]["id"]
    with Stubber(api.s3) as stubber:
        stubber.add_response("head_object", {"ContentLength": 5, "ContentType": "image/jpeg", "ETag": '"old"'})
        stubber.add_client_error("copy_object", service_error_code="PreconditionFailed", http_status_code=412)
        stubber.add_response("delete_object", {})
        response = api.client.post(f"/files/{file_id}/complete", headers=api.auth())
    assert response.status_code == 409
    assert api.client.get("/files", headers=api.auth()).json()[0]["status"] == "pending"
    put_upload(api, upload)
    assert api.client.post(f"/files/{file_id}/complete", headers=api.auth()).status_code == 200
