"""SecretStore (ADR-0009): integration credentials encrypted at rest with AES-256-GCM.

Associated data binds each ciphertext to its row and purpose (``"{secret_id}:{purpose}"``), so a
ciphertext copied into another row or re-labelled with another purpose fails to decrypt.
The API layer must never return plaintext — only ``configured`` + ``mask()``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import EncryptedBlob, SecretCipher
from app.core.errors import NotFoundError
from app.domains.identity.models import Secret


def _aad(secret_id: int, purpose: str) -> str:
    return f"{secret_id}:{purpose}"


def mask(plaintext: str, visible: int = 4) -> str:
    """``••••••••wxyz`` style hint (last chars only); short values are fully hidden."""
    if len(plaintext) <= visible * 3:
        return "•" * 8
    return f"{'•' * 8}{plaintext[-visible:]}"


class SecretStore:
    def __init__(self, cipher: SecretCipher) -> None:
        self._cipher = cipher

    async def put(
        self,
        db: AsyncSession,
        *,
        purpose: str,
        plaintext: str,
        workspace_id: int | None = None,
        description: str | None = None,
    ) -> Secret:
        row = Secret(
            workspace_id=workspace_id,
            purpose=purpose,
            ciphertext=b"",
            nonce=b"",
            key_version=0,
            description=description,
        )
        db.add(row)
        await db.flush()  # need the id for the associated data
        self._encrypt_into(row, plaintext)
        await db.flush()
        return row

    async def replace(self, db: AsyncSession, secret_id: int, plaintext: str) -> Secret:
        row = await self._load(db, secret_id)
        self._encrypt_into(row, plaintext)
        await db.flush()
        return row

    async def get(self, db: AsyncSession, secret_id: int, *, workspace_id: int | None = None) -> str:
        row = await self._load(db, secret_id, workspace_id=workspace_id)
        blob = EncryptedBlob(ciphertext=row.ciphertext, nonce=row.nonce, key_version=row.key_version)
        return self._cipher.decrypt(blob, purpose=_aad(row.id, row.purpose))

    async def delete(self, db: AsyncSession, secret_id: int) -> None:
        await db.delete(await self._load(db, secret_id))
        await db.flush()

    def _encrypt_into(self, row: Secret, plaintext: str) -> None:
        blob = self._cipher.encrypt(plaintext, purpose=_aad(row.id, row.purpose))
        row.ciphertext, row.nonce, row.key_version = blob.ciphertext, blob.nonce, blob.key_version

    @staticmethod
    async def _load(db: AsyncSession, secret_id: int, *, workspace_id: int | None = None) -> Secret:
        stmt = select(Secret).where(Secret.id == secret_id)
        if workspace_id is not None:
            stmt = stmt.where(Secret.workspace_id == workspace_id)
        row = await db.scalar(stmt)
        if row is None:
            raise NotFoundError("Secret not found")
        return row
