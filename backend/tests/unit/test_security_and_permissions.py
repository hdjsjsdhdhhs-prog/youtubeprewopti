from types import SimpleNamespace

import pytest

from app.api.deps import PERMISSION_MIN_ROLE, Permission
from app.core.security import hash_password, token_hash, verify_password
from app.domains.identity.models import WorkspaceRole
from app.domains.identity.service import csrf_valid, normalize_email, slugify


def test_password_hash_roundtrip_and_min_length():
    h = hash_password("a-long-enough-password")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "a-long-enough-password")
    assert not verify_password(h, "wrong-password-123")
    assert not verify_password("not-a-hash", "whatever")
    with pytest.raises(ValueError):
        hash_password("short")


def test_email_and_slug_normalization():
    assert normalize_email("  Foo@Bar.COM ") == "foo@bar.com"
    assert slugify("My Work Space!") == "my-work-space"
    assert slugify("!!!") == "workspace"


def _ctx(csrf: str):
    return SimpleNamespace(session=SimpleNamespace(csrf_hash=token_hash(csrf)))


def test_csrf_double_submit_bound_to_session():
    ctx = _ctx("tok")
    assert csrf_valid(ctx, "tok", "tok")
    assert not csrf_valid(ctx, "tok", None)
    assert not csrf_valid(ctx, None, "tok")
    assert not csrf_valid(ctx, "tok", "other")
    # cookie and header agree, but the token was not issued for this session
    assert not csrf_valid(ctx, "forged", "forged")


def test_every_permission_has_a_role_and_sending_requires_operator():
    assert set(PERMISSION_MIN_ROLE) == set(Permission)
    assert PERMISSION_MIN_ROLE[Permission.OUTREACH_SEND] == WorkspaceRole.OPERATOR
    assert PERMISSION_MIN_ROLE[Permission.READ] == WorkspaceRole.VIEWER
