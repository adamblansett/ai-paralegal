import asyncio
from pathlib import Path

from app.storage.base import StorageBackend


class LocalStorageBackend(StorageBackend):
    """Local filesystem storage backend with path-traversal protection."""

    backend_name = "local"

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_key(self, key: str) -> Path:
        clean_key = key.lstrip("/\\")
        if not clean_key:
            raise ValueError("Storage key must not be empty.")
        candidate = (self.root_dir / clean_key).resolve()
        if candidate != self.root_dir and self.root_dir not in candidate.parents:
            raise ValueError(f"Unsafe storage key outside root directory: {key}")
        return candidate

    def _write_sync(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(data)
        tmp_path.replace(path)

    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        _ = content_type
        target = self._resolve_key(key)
        await asyncio.to_thread(self._write_sync, target, data)
        return key.lstrip("/\\").replace("\\", "/")

    async def get_bytes(self, key: str) -> bytes:
        target = self._resolve_key(key)
        if not target.exists():
            raise FileNotFoundError(f"Stored object not found: {key}")
        return await asyncio.to_thread(target.read_bytes)

    async def delete(self, key: str) -> None:
        target = self._resolve_key(key)
        if target.exists():
            await asyncio.to_thread(target.unlink)

    async def exists(self, key: str) -> bool:
        target = self._resolve_key(key)
        return await asyncio.to_thread(target.exists)

    def get_uri(self, key: str) -> str:
        return f"file://{self._resolve_key(key)}"
