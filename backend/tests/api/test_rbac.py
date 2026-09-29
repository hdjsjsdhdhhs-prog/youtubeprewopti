from typing import Annotated

import pytest
from conftest import add_member, csrf_headers, login
from fastapi import APIRouter, Depends

from app.api.deps import Permission, require
from app.domains.identity.models import WorkspaceRole
from app.domains.identity.service import AuthContext


@pytest.fixture
def app(app):
    """Attach probe endpoints guarded by each permission."""
    router = APIRouter(prefix="/api/_probe")
    for perm in Permission:
        def _make(p: Permission):
            async def _probe(ctx: Annotated[AuthContext, Depends(require(p))]) -> dict:
                return {"ok": True, "workspace_id": ctx.workspace.id}
            return _probe
        router.add_api_route(f"/{perm.value}", _make(perm), methods=["POST"])
    app.include_router(router)
    return app


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (WorkspaceRole.VIEWER, {Permission.READ}),
        (WorkspaceRole.OPERATOR, {Permission.READ, Permission.WRITE, Permission.OUTREACH_SEND}),
        (WorkspaceRole.ADMIN, set(Permission)),
        (WorkspaceRole.OWNER, set(Permission)),
    ],
)
async def test_permission_matrix(client, db, owner, settings, role, allowed):
    owner_user, ws = owner
    if role == WorkspaceRole.OWNER:
        email = owner_user.email
    else:
        email = f"{role.value}@example.com"
        await add_member(db, ws, email, role)
    assert (await login(client, email)).status_code == 200

    for perm in Permission:
        r = await client.post(f"/api/_probe/{perm.value}", headers=csrf_headers(client, settings))
        expected = 200 if perm in allowed else 403
        assert r.status_code == expected, (role, perm, r.text)
        if expected == 403:
            assert r.json()["error"]["code"] == "forbidden"


async def test_protected_endpoint_requires_session(client):
    r = await client.post("/api/_probe/read")
    assert r.status_code == 401
