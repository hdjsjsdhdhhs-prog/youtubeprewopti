"""Contacts (Phase 6, ТЗ §18–19): public contacts of channels, manual contacts, statuses, suppression list."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import DbSession, Paging, Permission, ReadAuth, WriteAuth, require
from app.domains.contacts import service
from app.domains.contacts.schemas import (
    ChannelContacts,
    ContactChange,
    ContactScanStart,
    ContactScanStartResult,
    ContactStats,
    ContactStatusIn,
    ManualContactIn,
    SuppressionIn,
    SuppressionList,
    SuppressionOut,
)
from app.domains.identity.service import AuthContext

router = APIRouter(tags=["contacts"])
AdminAuth = Annotated[AuthContext, Depends(require(Permission.MANAGE_INTEGRATIONS))]


@router.get("/channels/{channel_id}/contacts", response_model=ChannelContacts)
async def channel_contacts(channel_id: int, ctx: ReadAuth, db: DbSession) -> ChannelContacts:
    """Public contacts found in the channel's own texts + this workspace's manual ones, with the workspace's
    statuses. ``status``: ``not_scanned`` / ``found`` / ``not_found`` (scanned, nothing usable — ТЗ §18)."""
    return await service.channel_contacts(db, ctx.workspace.id, channel_id)


@router.post("/channels/{channel_id}/contacts/scan", response_model=ContactChange)
async def scan_channel(channel_id: int, ctx: WriteAuth, db: DbSession) -> ContactChange:
    """Scan the channel description and the newest stored video descriptions now (deterministic, free, no
    network) and rescore the lead."""
    return await service.scan_channel_now(db, ctx.workspace.id, ctx.user.id, channel_id)


@router.post(
    "/channels/{channel_id}/contacts", response_model=ContactChange, status_code=status.HTTP_201_CREATED
)
async def add_contact(channel_id: int, body: ManualContactIn, ctx: WriteAuth, db: DbSession) -> ContactChange:
    """A contact found by hand (visible to this workspace only). 422 ``invalid_contact``, 409
    ``contact_exists``."""
    return await service.add_manual_contact(db, ctx.workspace.id, ctx.user.id, channel_id, body)


@router.patch("/contacts/{contact_id}", response_model=ContactChange)
async def set_contact_status(
    contact_id: int, body: ContactStatusIn, ctx: WriteAuth, db: DbSession
) -> ContactChange:
    """This workspace's status of the contact. ``opted_out`` adds the value to the suppression list and is
    final here (409 ``opted_out_final``)."""
    return await service.set_contact_status(db, ctx.workspace.id, ctx.user.id, contact_id, body)


@router.delete("/contacts/{contact_id}", response_model=ContactChange)
async def delete_contact(contact_id: int, ctx: WriteAuth, db: DbSession) -> ContactChange:
    """Only manual contacts (409 ``contact_not_manual``: mark a public one invalid instead)."""
    return await service.delete_manual_contact(db, ctx.workspace.id, ctx.user.id, contact_id)


@router.get("/projects/{project_id}/contacts/stats", response_model=ContactStats)
async def project_contact_stats(project_id: int, ctx: ReadAuth, db: DbSession) -> ContactStats:
    return await service.project_contact_stats(db, ctx.workspace.id, project_id)


@router.post(
    "/projects/{project_id}/contacts/scan",
    response_model=ContactScanStartResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def scan_project(
    project_id: int, body: ContactScanStart, ctx: WriteAuth, db: DbSession
) -> ContactScanStartResult:
    """One ``contact_extraction`` job for the project's channels not scanned with the current extractor
    (``rescan`` — all of them), at most 5000 (``remaining``). Leads are rescored when it finishes."""
    return await service.start_project_scan(db, ctx.workspace.id, ctx.user.id, project_id, body)


@router.get("/suppression-list", response_model=SuppressionList)
async def list_suppression(ctx: ReadAuth, db: DbSession, page: Paging) -> SuppressionList:
    return await service.list_suppression(db, ctx.workspace.id, limit=page.limit, offset=page.offset)


@router.post("/suppression-list", response_model=SuppressionOut, status_code=status.HTTP_201_CREATED)
async def add_suppression(body: SuppressionIn, ctx: WriteAuth, db: DbSession) -> SuppressionOut:
    """Never contact this value (idempotent). Leads of channels with it are rescored."""
    return await service.add_suppression(db, ctx.workspace.id, ctx.user.id, body)


@router.delete("/suppression-list/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_suppression(entry_id: int, ctx: AdminAuth, db: DbSession) -> Response:
    """Admin only: undoes an opt-out (audited)."""
    await service.remove_suppression(db, ctx.workspace.id, ctx.user.id, entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
