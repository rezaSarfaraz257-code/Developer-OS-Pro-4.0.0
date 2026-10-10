"""Persistent media storage with an S3-compatible production backend.

Set S3_BUCKET and credentials on the API service in production. Local media is
kept for development only; Render's container filesystem is not durable.
"""
import mimetypes
import os
from io import BytesIO
from urllib.parse import quote

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.files import File
from django.core.files.storage import FileSystemStorage, Storage
from django.core.exceptions import ImproperlyConfigured


class ProfileMediaStorage(Storage):
    """Use S3/R2 for durable production media and local disk in development."""

    def __init__(self, location=None, base_url=None):
        self.local = FileSystemStorage(
            location=location or settings.MEDIA_ROOT,
            base_url=base_url or settings.MEDIA_URL,
        )

    @property
    def bucket(self):
        return os.getenv("S3_BUCKET", "").strip()

    def _client(self):
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
            region_name=os.getenv("S3_REGION", "us-east-1"),
            aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID") or None,
            aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY") or None,
        )

    def _require_bucket(self):
        if not self.bucket:
            if not settings.DEBUG:
                raise ImproperlyConfigured(
                    "Profile photo uploads require persistent object storage. "
                    "Configure S3_BUCKET, S3_ACCESS_KEY_ID, and S3_SECRET_ACCESS_KEY."
                )
            return False
        return True

    def _open(self, name, mode="rb"):
        if not self._require_bucket():
            return self.local.open(name, mode)
        response = self._client().get_object(Bucket=self.bucket, Key=name)
        return File(BytesIO(response["Body"].read()), name=name)

    def _save(self, name, content):
        if not self._require_bucket():
            return self.local.save(name, content)

        client = self._client()
        content_type = (
            getattr(content, "content_type", None)
            or mimetypes.guess_type(name)[0]
            or "application/octet-stream"
        )
        extra_args = {"ContentType": content_type}
        # Cloudflare R2 and some S3-compatible services do not support S3 SSE.
        if not os.getenv("S3_ENDPOINT_URL") and os.getenv("S3_SERVER_SIDE_ENCRYPTION"):
            extra_args["ServerSideEncryption"] = os.getenv("S3_SERVER_SIDE_ENCRYPTION")
        content.seek(0)
        client.upload_fileobj(content, self.bucket, name, ExtraArgs=extra_args)
        return name

    def delete(self, name):
        if not self.bucket:
            return self.local.delete(name)
        self._client().delete_object(Bucket=self.bucket, Key=name)

    def exists(self, name):
        if not self.bucket:
            return self.local.exists(name)
        try:
            self._client().head_object(Bucket=self.bucket, Key=name)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def size(self, name):
        if not self.bucket:
            return self.local.size(name)
        return self._client().head_object(Bucket=self.bucket, Key=name)["ContentLength"]

    def url(self, name):
        if not self.bucket:
            return self.local.url(name)
        public_base = os.getenv("S3_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if public_base:
            return f"{public_base}/{quote(name, safe='/')}"
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": name},
            ExpiresIn=3600,
        )

    def path(self, name):
        if self.bucket:
            raise NotImplementedError("S3-backed media does not have a local filesystem path.")
        return self.local.path(name)

    def get_available_name(self, name, max_length=None):
        if not self.bucket:
            return self.local.get_available_name(name, max_length=max_length)
        return super().get_available_name(name, max_length=max_length)

    def listdir(self, path):
        if not self.bucket:
            return self.local.listdir(path)
        raise NotImplementedError("Directory listing is not supported for object storage.")

    def deconstruct(self):
        return ("api.avatar_storage.ProfileMediaStorage", (), {})
