import base64
import os
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import update

from app.core import idempotency
from app.core.crypto import SecretCipher
from app.core.errors import ConflictError, NotFoundError
from app.core.secrets import SecretStore, mask
from app.domains.identity.models import IdempotencyKey, Secret


@pytest.fixture
def store() -> SecretStore:
    return SecretStore(SecretCipher(base64.b64encode(os.urandom(32)).decode()))


# --------------------------------------------------------------------------- SecretStore
async def test_secret_roundtrip_is_encrypted_at_rest(db, owner, store):
    _, ws = owner
    row = await store.put(db, purpose="openai_api_key", plaintext="sk-test-1234567890", workspace_id=ws.id)
    assert b"sk-test" not in row.ciphertext and len(row.nonce) == 12 and row.key_version == 1
    assert await store.get(db, row.id) == "sk-test-1234567890"
    assert await store.get(db, row.id, workspace_id=ws.id) == "sk-test-1234567890"


async def test_secret_is_scoped_to_workspace(db, owner, other_owner, store):
    row = await store.put(db, purpose="smtp", plaintext="pw-abcdefgh", workspace_id=owner[1].id)
    with pytest.raises(NotFoundError):
        await store.get(db, row.id, workspace_id=other_owner[1].id)


async def test_ciphertext_is_bound_to_row_and_purpose(db, store):
    a = await store.put(db, purpose="smtp", plaintext="first-secret")
    b = await store.put(db, purpose="smtp", plaintext="second-secret")
    # copy a's ciphertext into b's row -> associated data (row id) no longer matches
    await db.execute(update(Secret).where(Secret.id == b.id).values(ciphertext=a.ciphertext, nonce=a.nonce))
    await db.refresh(b)
    with pytest.raises(InvalidTag):
        await store.get(db, b.id)
    # relabel purpose -> fails too
    await db.execute(update(Secret).where(Secret.id == a.id).values(purpose="telegram"))
    await db.refresh(a)
    with pytest.raises(InvalidTag):
        await store.get(db, a.id)


async def test_secret_replace_and_delete(db, store):
    row = await store.put(db, purpose="vk", plaintext="old-value")
    old_nonce = row.nonce
    await store.replace(db, row.id, "new-value")
    assert await store.get(db, row.id) == "new-value" and row.nonce != old_nonce
    await store.delete(db, row.id)
    with pytest.raises(NotFoundError):
        await store.get(db, row.id)


def test_mask_never_reveals_short_values():
    assert mask("sk-abcdefghijklmnop") == "••••••••mnop"
    assert mask("short") == "••••••••"


# --------------------------------------------------------------------------- Idempotency
async def test_idempotency_claim_complete_replay(db, owner):
    ws = owner[1].id
    h = idempotency.request_hash({"a": 1, "b": [1, 2]})
    assert h == idempotency.request_hash({"b": [1, 2], "a": 1})  # key order does not matter

    assert await idempotency.claim(db, ws, "key-1", h) is None
    with pytest.raises(ConflictError) as in_progress:
        await idempotency.claim(db, ws, "key-1", h)
    assert in_progress.value.code == "idempotency_in_progress"

    await idempotency.complete(db, ws, "key-1", 201, {"id": 42})
    replay = await idempotency.claim(db, ws, "key-1", h)
    assert replay == idempotency.Replay(status=201, body={"id": 42})

    with pytest.raises(ConflictError) as reused:
        await idempotency.claim(db, ws, "key-1", idempotency.request_hash({"a": 2}))
    assert reused.value.code == "idempotency_key_reused"


async def test_idempotency_keys_are_per_workspace_and_expire(db, owner, other_owner):
    h = idempotency.request_hash({})
    assert await idempotency.claim(db, owner[1].id, "shared", h) is None
    assert await idempotency.claim(db, other_owner[1].id, "shared", h) is None  # independent namespace

    await db.execute(update(IdempotencyKey).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert await idempotency.claim(db, owner[1].id, "shared", idempotency.request_hash({"new": 1})) is None
    assert await idempotency.purge_expired(db) == 1  # the other workspace's expired key


@pytest.mark.parametrize("key", ["", "x" * 201, "bad\nkey"])
async def test_idempotency_rejects_invalid_keys(db, owner, key):
    with pytest.raises(idempotency.IdempotencyKeyInvalidError):
        await idempotency.claim(db, owner[1].id, key, "h")
