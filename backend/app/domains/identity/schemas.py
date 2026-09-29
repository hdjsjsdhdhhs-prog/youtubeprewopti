from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domains.identity.models import WorkspaceRole


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Not EmailStr: the login endpoint must not reveal which inputs are "valid" users.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str


class WorkspaceOut(BaseModel):
    id: int
    name: str
    slug: str


class MeResponse(BaseModel):
    user: UserOut
    workspace: WorkspaceOut
    role: WorkspaceRole
