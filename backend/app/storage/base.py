from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """Abstract object/file storage interface supporting local disk and S3-compatible stores."""

    backend_name: str

    @abstractmethod
    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        """Persist bytes under `key` and return the canonical storage key."""

    @abstractmethod
    async def get_bytes(self, key: str) -> bytes:
        """Retrieve raw bytes for `key`."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete object at `key` if present."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True if object exists at `key`."""

    @abstractmethod
    def get_uri(self, key: str) -> str:
        """Return a human-readable URI for the stored key."""

    async def generate_presigned_url(
        self,
        key: str,
        *,
        expires_in: int = 300,
        download_filename: str | None = None,
    ) -> str | None:
        """Return a short-lived signed download URL when supported by the storage backend."""
        _ = (key, expires_in, download_filename)
        return None
