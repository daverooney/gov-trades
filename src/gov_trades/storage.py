"""Object storage for raw documents: a local directory or Cloudflare R2.

Keys are POSIX-style relative paths such as ``raw/house/2026/P/20031234.pdf``.
The local backend maps a key to ``<root>/<key>``; R2 uses it verbatim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .config import Settings


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"bad storage key: {key!r}")
    return key


class LocalStorage:
    """Files under a root directory. The default when R2 is not configured."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class R2Storage:
    """Cloudflare R2 through the S3 API. ``boto3`` is imported lazily so the
    local backend has no cloud dependency at import time."""

    def __init__(self, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str) -> None:
        import boto3  # noqa: PLC0415

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=_check_key(key), Body=data)

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError  # noqa: PLC0415

        try:
            self._client.head_object(Bucket=self.bucket, Key=_check_key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True


def storage_from_settings(settings: Settings) -> Storage:
    if settings.r2_configured:
        return R2Storage(
            endpoint=settings.r2_account_endpoint,  # type: ignore[arg-type]
            bucket=settings.r2_bucket,  # type: ignore[arg-type]
            access_key_id=settings.r2_access_key_id,  # type: ignore[arg-type]
            secret_access_key=settings.r2_secret_access_key,  # type: ignore[arg-type]
        )
    return LocalStorage(settings.storage_local_root)
