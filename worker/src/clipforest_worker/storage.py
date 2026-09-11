"""R2 / S3-compatible storage client (worker side).

Uses the worker's own internal endpoint; presigned GET URLs let FFmpeg read
source ranges over HTTP (range requests) without downloading multi-GB files.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from . import errors
from .config import Settings


class Storage:
    def __init__(self, s: Settings):
        self.bucket = s.s3_bucket
        cfg = Config(
            retries={"max_attempts": 5, "mode": "standard"},
            s3={"addressing_style": "path" if s.s3_force_path_style else "auto"},
            connect_timeout=10,
            read_timeout=120,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key_id,
            aws_secret_access_key=s.s3_secret_access_key,
            config=cfg,
        )

    async def head(self, key: str) -> dict | None:
        def _head():
            try:
                return self.client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise

        try:
            return await asyncio.to_thread(_head)
        except (ClientError, BotoCoreError) as exc:
            raise errors.storage_failed(str(exc)) from exc

    async def download(self, key: str, dest: Path) -> None:
        try:
            await asyncio.to_thread(self.client.download_file, self.bucket, key, str(dest))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                raise errors.upload_missing() from exc
            raise errors.storage_failed(str(exc)) from exc
        except BotoCoreError as exc:
            raise errors.storage_failed(str(exc)) from exc

    async def upload(self, src: Path, key: str, content_type: str) -> None:
        try:
            await asyncio.to_thread(
                self.client.upload_file, str(src), self.bucket, key, ExtraArgs={"ContentType": content_type}
            )
        except (ClientError, BotoCoreError) as exc:
            raise errors.storage_failed(str(exc)) from exc

    def presign_get(self, key: str, ttl_sec: int = 6 * 3600) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl_sec)

    async def delete_keys(self, keys: list[str]) -> int:
        keys = [k for k in keys if k]
        if not keys:
            return 0

        def _delete():
            deleted = 0
            for i in range(0, len(keys), 1000):
                chunk = keys[i : i + 1000]
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True})
                deleted += len(chunk)
            return deleted

        try:
            return await asyncio.to_thread(_delete)
        except (ClientError, BotoCoreError) as exc:
            raise errors.storage_failed(str(exc)) from exc

    async def delete_prefix(self, prefix: str) -> int:
        if not prefix.startswith("users/") or not prefix.endswith("/"):
            raise ValueError(f"refusing to delete unscoped prefix {prefix!r}")

        def _list():
            keys: list[str] = []
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
            return keys

        try:
            keys = await asyncio.to_thread(_list)
        except (ClientError, BotoCoreError) as exc:
            raise errors.storage_failed(str(exc)) from exc
        return await self.delete_keys(keys)

    async def abort_multipart_uploads(self, prefix: str) -> None:
        def _abort():
            resp = self.client.list_multipart_uploads(Bucket=self.bucket, Prefix=prefix)
            for up in resp.get("Uploads", []) or []:
                self.client.abort_multipart_upload(Bucket=self.bucket, Key=up["Key"], UploadId=up["UploadId"])

        try:
            await asyncio.to_thread(_abort)
        except (ClientError, BotoCoreError):
            pass
