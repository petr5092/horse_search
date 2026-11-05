"""Read-only inspection and setup plan; never changes bucket settings or ACL."""
import json
import sys

from botocore.exceptions import ClientError

from app.files.storage import get_storage


def inspect(storage):
    report = {"bucket": storage.bucket, "mode": "read-only", "current": {}}
    operations = {
        "acl": ("get_bucket_acl", "Grants"),
        "cors": ("get_bucket_cors", "CORSRules"),
        "lifecycle": ("get_bucket_lifecycle_configuration", "Rules"),
    }
    for label, (method, field) in operations.items():
        try:
            report["current"][label] = getattr(storage.client, method)(Bucket=storage.bucket).get(field, [])
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            report["current"][label] = {"status": code}
    grants = report["current"]["acl"]
    report["public_acl_detected"] = (any(
        grant.get("Grantee", {}).get("URI", "").endswith(("/AllUsers", "/AuthenticatedUsers"))
        for grant in grants) if isinstance(grants, list) else None)
    report["plan"] = {
        "privacy": "Disable anonymous read/list/config in Yandex Cloud console; inspect bucket policy too. ACL alone cannot prove privacy.",
        "cors": "Merge rule for the exact frontend origin, methods POST/GET/HEAD, headers *. Do not replace existing rules blindly.",
        "lifecycle": {"ID": "horse-staging-expiration", "Status": "Enabled",
                      "Filter": {"Prefix": "staging/"}, "Expiration": {"Days": 1}},
        "versions": "If versioning is enabled, separately decide noncurrent-version expiration; no version deletion is configured automatically.",
    }
    return report


def main():
    try:
        print(json.dumps(inspect(get_storage()), ensure_ascii=False, indent=2))
    except Exception:
        print("Inspection failed: check static credentials, read permissions, and network; sensitive details suppressed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
