"""HTTP ``Idempotency-Key`` support for expensive POSTs (ADR-0011).

Usage inside a request transaction::

    replay = await claim(db, ws_id, key, request_hash(body))
    if replay:                      # same key + same body already completed
        return JSONResponse(replay.body, status_code=replay.status)
    ...do the work...
    await complete(db, ws_id, key, 201, response_body)

The claim row lives in the request transaction: if the handler fails, the rollback releases the key.
A concurrent request with the same key blocks on the unique index until the first one commits and
then replays its stored response.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError
from app.domains.identity.models import IdempotencyKey

DEFAULT_TTL = timedelta(hours=24)
MAX_KEY_LENGTH = 200


class IdempotencyKeyInvalidError(AppError):
    status_code = 400
    code = "idempotency_key_invalid"


@dataclass(frozen=True)
class Replay:
    status: int
    body: dict[str, Any]


def request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def claim(
    db: AsyncSession, workspace_id: int, key: str, req_hash: str, *, ttl: timedelta = DEFAULT_TTL
) -> Replay | None:
    """Reserve ``key`` for this request. Returns a Replay if the same request already completed."""
    if not key or len(key) > MAX_KEY_LENGTH or not key.isprintable():
        raise IdempotencyKeyInvalidError(f"Idempotency-Key must be 1..{MAX_KEY_LENGTH} printable characters")
    now = datetime.now(UTC)
    await db.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.workspace_id == workspace_id,
            IdempotencyKey.key == key,
            IdempotencyKey.expires_at <= now,
        )
    )
    inserted = await db.scalar(
        insert(IdempotencyKey)
        .values(workspace_id=workspace_id, key=key, request_hash=req_hash, expires_at=now + ttl)
        .on_conflict_do_nothing(index_elements=["workspace_id", "key"])
        .returning(IdempotencyKey.id)
    )
    if inserted is not None:
        return None

    existing = await db.scalar(
        select(IdempotencyKey).where(IdempotencyKey.workspace_id == workspace_id, IdempotencyKey.key == key)
    )
    if existing is None:  # expired and removed concurrently; extremely unlikely
        raise ConflictError("Idempotency-Key is being processed, retry later", code="idempotency_in_progress")
    if existing.request_hash != req_hash:
        raise ConflictError(
            "Idempotency-Key was already used with a different request", code="idempotency_key_reused"
        )
    if existing.response_status is None:
        raise ConflictError(
            "A request with this Idempotency-Key is still in progress", code="idempotency_in_progress"
        )
    return Replay(status=existing.response_status, body=existing.response_body or {})


async def complete(db: AsyncSession, workspace_id: int, key: str, status: int, body: dict[str, Any]) -> None:
    await db.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.workspace_id == workspace_id, IdempotencyKey.key == key)
        .values(response_status=status, response_body=body)
    )


async def purge_expired(db: AsyncSession) -> int:
    stmt = delete(IdempotencyKey).where(IdempotencyKey.expires_at <= datetime.now(UTC))
    result = cast(CursorResult[Any], await db.execute(stmt))  # DML without RETURNING => CursorResult
    return result.rowcount or 0
