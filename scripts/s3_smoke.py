"""Opt-in real S3 HTTP smoke check. No database or bucket configuration changes."""
import argparse
import hashlib
import secrets
import sys
from types import SimpleNamespace
from uuid import uuid4

import requests

from app.files.storage import get_storage


class SmokeFailure(Exception):
    pass


def smoke(storage, http=requests):
    if storage.client.get_bucket_versioning(Bucket=storage.bucket).get("Status"):
        raise SmokeFailure("Smoke requires an unversioned bucket to guarantee complete cleanup")
    token = uuid4().hex
    staging_key = f"staging/smoke/{token}"
    final_key = f"originals/smoke/{token}"
    body = secrets.token_bytes(64)
    expected = hashlib.sha256(body).hexdigest()
    file = SimpleNamespace(staging_key=staging_key, size=len(body),
                           content_type="application/octet-stream", expected_sha256=expected)
    try:
        form = storage.upload_form(staging_key, len(body), file.content_type)
        response = http.post(form["url"], data=form["fields"],
                             files={"file": ("smoke.bin", body, file.content_type)}, timeout=30)
        if response.status_code not in {200, 204}:
            raise SmokeFailure(f"Presigned POST returned HTTP {response.status_code}")
        if storage.finalize(file, final_key) != expected:
            raise SmokeFailure("Server-side SHA-256 mismatch")
        url = storage.download_url(final_key, "smoke.bin")
        downloaded = http.get(url, timeout=30)
        if downloaded.status_code != 200 or hashlib.sha256(downloaded.content).hexdigest() != expected:
            raise SmokeFailure("Presigned GET or SHA-256 verification failed")
        # Removing all query parameters removes SigV4 credentials. A public bucket
        # or policy must not silently turn private originals into public objects.
        anonymous = http.get(url.split("?", 1)[0], timeout=30)
        if anonymous.status_code != 403:
            raise SmokeFailure(f"Anonymous GET must return 403; got HTTP {anonymous.status_code}")
    finally:
        # Only these two randomly generated keys are ever deleted.
        errors = []
        for key in (staging_key, final_key):
            try:
                storage.delete(key)
            except Exception:
                errors.append(key)
        if errors:
            raise SmokeFailure(f"Cleanup failed for smoke operation {token}; retry cleanup with operator access")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Authorize tiny writes and deletion of this test's own objects")
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    try:
        smoke(get_storage())
    except SmokeFailure as exc:
        print(f"S3 smoke FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # SDK/requests exceptions may include signed URLs or form credentials.
        print("S3 smoke FAILED: storage, credentials, or network error (sensitive details suppressed)", file=sys.stderr)
        return 1
    print("S3 smoke OK: POST, conditional copy, SHA-256, presigned GET, anonymous denial, cleanup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
