from app.core.config import Settings, get_settings
from app.storage.base import StorageBackend
from app.storage.local import LocalStorageBackend
from app.storage.s3 import S3StorageBackend


def get_storage_backend(settings: Settings | None = None) -> StorageBackend:
    """Instantiate configured storage backend."""
    cfg = settings or get_settings()
    if cfg.storage_backend == "s3":
        return S3StorageBackend(
            bucket=cfg.s3_bucket,
            region=cfg.s3_region,
            endpoint_url=cfg.s3_endpoint_url or None,
            access_key_id=cfg.s3_access_key_id or None,
            secret_access_key=cfg.s3_secret_access_key or None,
            server_side_encryption=cfg.s3_server_side_encryption,
            kms_key_id=cfg.s3_kms_key_id or None,
        )
    return LocalStorageBackend(cfg.local_storage_dir)


__all__ = [
    "LocalStorageBackend",
    "S3StorageBackend",
    "StorageBackend",
    "get_storage_backend",
]
