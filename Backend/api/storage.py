"""Object-storage adapter.

Local filesystem is the development backend. When S3_BUCKET is configured,
exports and other generated objects can use S3-compatible object storage
(AWS S3, Cloudflare R2, MinIO, etc.) without changing application code.
"""
import os
from pathlib import Path


def _s3():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
        region_name=os.getenv("S3_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
    )


def put_file(local_path, key, content_type="application/octet-stream"):
    bucket = os.getenv("S3_BUCKET", "").strip()
    if bucket:
        client = _s3()
        client.upload_file(str(local_path), bucket, key, ExtraArgs={"ContentType": content_type, "ServerSideEncryption": os.getenv("S3_SERVER_SIDE_ENCRYPTION", "AES256")})
        return {"provider": "s3", "bucket": bucket, "key": key}
    return {"provider": "local", "bucket": "", "key": str(local_path)}


def presigned_get(key, expires=900):
    bucket = os.getenv("S3_BUCKET", "").strip()
    if not bucket:
        return None
    return _s3().generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires)
