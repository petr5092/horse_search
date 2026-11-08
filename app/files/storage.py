"""All methods are synchronous; the API calls them in Starlette's thread pool."""
import hashlib
from functools import lru_cache
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError
from fastapi import HTTPException

from app.config import settings


class StorageConfigurationError(Exception):
    pass


class InvalidUpload(Exception):
    pass


class S3Storage:
    def __init__(self, client, bucket):
        self.client = client
        self.bucket = bucket

    def upload_form(self, key, size, content_type):
        return self.client.generate_presigned_post(
            Bucket=self.bucket, Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", size, size]],
            ExpiresIn=settings.S3_UPLOAD_EXPIRES_SECONDS,
        )

    def finalize(self, file, final_key):
        head = self.client.head_object(Bucket=self.bucket, Key=file.staging_key)
        if head["ContentLength"] != file.size or head.get("ContentType") != file.content_type:
            raise InvalidUpload("Uploaded size or MIME type does not match the request")
        # A replay may overwrite staging, but cannot replace the accepted original.
        self.client.copy_object(
            Bucket=self.bucket, Key=final_key,
            CopySource={"Bucket": self.bucket, "Key": file.staging_key},
            CopySourceIfMatch=head["ETag"], MetadataDirective="REPLACE",
            ContentType=file.content_type,
        )
        response = self.client.get_object(Bucket=self.bucket, Key=final_key)
        digest = hashlib.sha256()
        size = 0
        body = response["Body"]
        try:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                size += len(chunk)
                if size > file.size:
                    raise InvalidUpload("Uploaded size does not match the request")
                digest.update(chunk)
        finally:
            body.close()
        checksum = digest.hexdigest()
        if size != file.size or (file.expected_sha256 and checksum != file.expected_sha256):
            raise InvalidUpload("Uploaded content checksum or size does not match the request")
        return checksum

    def delete(self, *keys):
        for key in keys:
            if key:
                self.client.delete_object(Bucket=self.bucket, Key=key)

    def view_url(self, key, content_type):
        if content_type not in {'image/png', 'image/jpeg', 'image/webp'}:
            raise InvalidUpload('Only supported image MIME types can be viewed')
        return self.client.generate_presigned_url(
            'get_object', Params={'Bucket': self.bucket, 'Key': key,
                'ResponseContentDisposition': 'inline', 'ResponseContentType': content_type},
            ExpiresIn=settings.S3_DOWNLOAD_EXPIRES_SECONDS,
        )

    def download_url(self, key, name):
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key,
                "ResponseContentDisposition": "attachment; filename*=UTF-8''" + quote(name, safe=""),
                "ResponseContentType": "application/octet-stream"},
            ExpiresIn=settings.S3_DOWNLOAD_EXPIRES_SECONDS,
        )


@lru_cache
def _configured_storage():
    access_key = (settings.S3_ACCESS_KEY_ID or "").strip()
    secret_key = (settings.S3_SECRET_ACCESS_KEY or "").strip()
    if not access_key or not secret_key:
        raise StorageConfigurationError("Set both S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY")
    client = boto3.client(
        "s3", endpoint_url=settings.S3_ENDPOINT_URL,
        region_name=settings.S3_REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=settings.S3_SESSION_TOKEN,
        config=Config(signature_version="s3v4", s3={"addressing_style": settings.S3_ADDRESSING_STYLE},
                      request_checksum_calculation="when_required",
                      response_checksum_validation="when_required",
                      connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}),
    )
    return S3Storage(client, settings.S3_BUCKET)


def get_storage():
    try:
        return _configured_storage()
    except StorageConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    except BotoCoreError as exc:
        raise HTTPException(503, "File storage is not configured or is unavailable") from exc
