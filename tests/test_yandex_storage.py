import base64
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from fastapi import HTTPException
from moto import mock_aws

from app.config import settings
from app.files import storage as module
from scripts.s3_smoke import SmokeFailure, smoke


@pytest.fixture
def configured(monkeypatch):
    module._configured_storage.cache_clear()
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "https://storage.yandexcloud.net")
    monkeypatch.setattr(settings, "S3_REGION", "ru-central1")
    monkeypatch.setattr(settings, "S3_BUCKET", "horse-dataset-vim")
    monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", "test-key")
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "test-secret")
    yield
    module._configured_storage.cache_clear()


def test_yandex_signature_and_checksum_compatibility(configured):
    storage = module.get_storage()
    form = storage.upload_form("staging/test", 5, "image/jpeg")
    assert form["url"] == "https://storage.yandexcloud.net/horse-dataset-vim"
    assert form["fields"]["x-amz-algorithm"] == "AWS4-HMAC-SHA256"
    assert "/ru-central1/s3/aws4_request" in form["fields"]["x-amz-credential"]
    policy = json.loads(base64.b64decode(form["fields"]["policy"]))
    assert ["content-length-range", 5, 5] in policy["conditions"]
    link = storage.download_url("originals/test", "horse.jpg")
    query = parse_qs(urlparse(link).query)
    assert "/ru-central1/s3/aws4_request" in query["X-Amz-Credential"][0]
    assert storage.client.meta.config.request_checksum_calculation == "when_required"
    assert storage.client.meta.config.response_checksum_validation == "when_required"


@pytest.mark.parametrize("access,secret", [(None, None), ("", ""), ("  ", "test"), ("test", None), (None, "test")])
def test_missing_or_partial_credentials_fail_before_sdk(configured, monkeypatch, access, secret):
    monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", access)
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", secret)
    def forbidden(*args, **kwargs):
        pytest.fail("Must not access AWS provider chain or metadata")
    monkeypatch.setattr(module.boto3, "client", forbidden)
    with pytest.raises(HTTPException) as caught:
        module.get_storage()
    assert caught.value.status_code == 503
    assert "both S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY" in caught.value.detail


@pytest.mark.parametrize("anonymous_status", [403, 200])
def test_smoke_verifies_privacy_and_cleans_only_own_keys(anonymous_status):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
        client.create_bucket(Bucket="test-files")
        client.put_object(Bucket="test-files", Key="originals/keep", Body=b"existing")
        storage = module.S3Storage(client, "test-files")
        class HTTP:
            body = None
            @classmethod
            def post(cls, url, data, files, **kwargs):
                cls.body = files["file"][1]
                client.put_object(Bucket="test-files", Key=data["key"], Body=cls.body, ContentType=data["Content-Type"])
                return SimpleNamespace(status_code=204)
            @classmethod
            def get(cls, url, **kwargs):
                return SimpleNamespace(status_code=200 if "?" in url else anonymous_status, content=cls.body)
        if anonymous_status == 200:
            with pytest.raises(SmokeFailure, match="Anonymous GET"):
                smoke(storage, HTTP)
        else:
            smoke(storage, HTTP)
        keys = [item["Key"] for item in client.list_objects_v2(Bucket="test-files")["Contents"]]
        assert keys == ["originals/keep"]


def test_inspector_reports_unknown_privacy_when_acl_unavailable():
    from botocore.stub import Stubber
    from scripts.s3_inspect import inspect
    client = boto3.client("s3", region_name="ru-central1", aws_access_key_id="test", aws_secret_access_key="test")
    storage = module.S3Storage(client, "test-files")
    with Stubber(client) as stubber:
        stubber.add_client_error("get_bucket_acl", service_error_code="AccessDenied", http_status_code=403)
        stubber.add_client_error("get_bucket_cors", service_error_code="NoSuchCORSConfiguration", http_status_code=404)
        stubber.add_client_error("get_bucket_lifecycle_configuration", service_error_code="NoSuchLifecycleConfiguration", http_status_code=404)
        report = inspect(storage)
    assert report["public_acl_detected"] is None
    assert report["current"]["acl"]["status"] == "AccessDenied"
