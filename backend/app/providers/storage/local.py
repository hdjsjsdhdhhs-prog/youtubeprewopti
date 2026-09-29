"""Local filesystem storage backend (default for self-hosted)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.providers.storage.base import StoredObject, content_key, sha256_hex


class LocalFSStorage:
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        # Prevent path traversal via crafted keys
        if self.root != p and self.root not in p.parents:
            raise ValueError("storage key escapes storage root")
        return p

    def put(self, data: bytes, ext: str) -> StoredObject:
        digest = sha256_hex(data)
        key = content_key(digest, ext)
        path = self._path(key)
        if path.exists():
            return StoredObject(key=key, sha256=digest, size=len(data), created=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)  # atomic on the same volume
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return StoredObject(key=key, sha256=digest, size=len(data), created=True)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
