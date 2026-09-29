"""Storage abstraction (ADR-0006). Keys are content-addressed: images/ab/abcdef….ext"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size: int
    created: bool  # False if identical content already existed


def content_key(sha256: str, ext: str, prefix: str = "images") -> str:
    ext = ext.lower().lstrip(".")
    if not ext.isalnum() or len(ext) > 5:
        raise ValueError(f"invalid extension: {ext!r}")
    return f"{prefix}/{sha256[:2]}/{sha256}.{ext}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StorageBackend(Protocol):
    name: str

    def put(self, data: bytes, ext: str) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...
