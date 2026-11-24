import asyncio
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.storage.base import StorageBackend


class S3StorageBackend(StorageBackend):
    """S3-compatible object storage backend (AWS S3, MinIO, Cloudflare R2) with SSE-KMS."""

    backend_name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        server_side_encryption: str = "AES256",
        kms_key_id: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("S3_BUCKET must be configured when STORAGE_BACKEND=s3.")
        self.bucket = bucket
        self.server_side_encryption = (
            "aws:kms" if kms_key_id else (server_side_encryption or "AES256")
        )
        self.kms_key_id = kms_key_id or None

        if client is not None:
            self._client = client
            return

        client_kwargs: dict[str, Any] = {"region_name": region}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        if access_key_id and secret_access_key:
            client_kwargs["aws_access_key_id"] = access_key_id
            client_kwargs["aws_secret_access_key"] = secret_access_key
        self._client = boto3.client("s3", **client_kwargs)

    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        clean_key = key.lstrip("/")
        put_kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": clean_key,
            "Body": data,
            "ContentType": content_type,
        }
        if self.server_side_encryption:
            put_kwargs["ServerSideEncryption"] = self.server_side_encryption
        if self.kms_key_id:
            put_kwargs["SSEKMSKeyId"] = self.kms_key_id

        await asyncio.to_thread(self._client.put_object, **put_kwargs)
        return clean_key

    async def get_bytes(self, key: str) -> bytes:
        clean_key = key.lstrip("/")

        def _read() -> bytes:
            resp = self._client.get_object(Bucket=self.bucket, Key=clean_key)
            body = resp["Body"].read()
            return bytes(body)

        return await asyncio.to_thread(_read)

    async def delete(self, key: str) -> None:
        clean_key = key.lstrip("/")
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=clean_key)

    async def exists(self, key: str) -> bool:
        clean_key = key.lstrip("/")

        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self.bucket, Key=clean_key)
                return True
            except ClientError:
                return False

        return await asyncio.to_thread(_head)

    async def generate_presigned_url(
        self,
        key: str,
        *,
        expires_in: int = 300,
        download_filename: str | None = None,
    ) -> str | None:
        clean_key = key.lstrip("/")
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": clean_key}
        if download_filename:
            safe_name = download_filename.replace('"', "").replace("\r", "").replace("\n", "")
            params["ResponseContentDisposition"] = f'attachment; filename="{safe_name}"'

        def _sign() -> str:
            return str(
                self._client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=expires_in,
                )
            )

        return await asyncio.to_thread(_sign)

    def get_uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key.lstrip('/')}"
