"""Thumbnail generation (Phase 5, ТЗ §12–14, §42–43): references, estimate / start per lead, results and
decisions on variants."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import AppSettings, DbSession, ReadAuth, Storage, WriteAuth
from app.domains.generation import service
from app.domains.generation.schemas import (
    GenerationEstimate,
    GenerationIn,
    GenerationStart,
    GenerationStartResult,
    LeadGenerations,
    ReferenceIn,
    ReferenceOut,
    ReferencePatch,
    VariantDecision,
)

router = APIRouter(tags=["generation"])


@router.post("/leads/{lead_id}/generation/estimate", response_model=GenerationEstimate)
async def estimate_generation(
    lead_id: int, body: GenerationIn, ctx: ReadAuth, db: DbSession, settings: AppSettings
) -> GenerationEstimate:
    """Brief built from the analysis (problems → directives, channel style, palette, instructions,
    references, earlier decisions), the exact prompt of variant A, the price per image and budgets, and the
    ``confirm_token``. Nothing is generated or charged."""
    return await service.estimate_generation(db, settings, ctx.workspace.id, lead_id, body)


@router.post(
    "/leads/{lead_id}/generations", response_model=GenerationStartResult, status_code=status.HTTP_202_ACCEPTED
)
async def start_generation(
    lead_id: int, body: GenerationStart, ctx: WriteAuth, db: DbSession, settings: AppSettings
) -> GenerationStartResult:
    """Queue the confirmed estimate (409 ``confirmation_required`` with the new token if anything changed,
    409 ``generation_running`` if one is already running for the lead). The confirmed total is the job's
    hard cost cap. The lead moves to ``generation_pending`` (via ``selected``)."""
    return await service.start_generation(db, settings, ctx.workspace.id, ctx.user.id, lead_id, body)


@router.get("/leads/{lead_id}/generations", response_model=LeadGenerations)
async def lead_generations(lead_id: int, ctx: ReadAuth, db: DbSession) -> LeadGenerations:
    """All generations of the lead (newest first) with their variants and the originals they redesigned —
    the data of the «Original vs Generated» comparison."""
    return await service.lead_generations(db, ctx.workspace.id, lead_id)


@router.post("/generated-thumbnails/{variant_id}/decision", response_model=LeadGenerations)
async def decide_variant(
    variant_id: int, body: VariantDecision, ctx: WriteAuth, db: DbSession
) -> LeadGenerations:
    """approve / reject / final («use as final free sample», one per lead) / reset. Logged as feedback."""
    return await service.decide_variant(db, ctx.workspace.id, ctx.user.id, variant_id, body)


@router.get("/references", response_model=list[ReferenceOut])
async def list_references(ctx: ReadAuth, db: DbSession) -> list[ReferenceOut]:
    return await service.list_references(db, ctx.workspace.id)


@router.post("/references", response_model=ReferenceOut, status_code=status.HTTP_201_CREATED)
async def create_reference(
    body: ReferenceIn, ctx: WriteAuth, db: DbSession, storage: Storage
) -> ReferenceOut:
    """Upload a style reference: the image as base64 (JPEG / PNG / WebP, ≤ 10 MB) plus title, description,
    style, category and tags."""
    return await service.create_reference(db, storage, ctx.workspace.id, ctx.user.id, body)


@router.patch("/references/{reference_id}", response_model=ReferenceOut)
async def update_reference(
    reference_id: int, body: ReferencePatch, ctx: WriteAuth, db: DbSession
) -> ReferenceOut:
    return await service.update_reference(db, ctx.workspace.id, ctx.user.id, reference_id, body)


@router.delete("/references/{reference_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reference(reference_id: int, ctx: WriteAuth, db: DbSession) -> Response:
    """Earlier generations keep the image they were made from."""
    await service.delete_reference(db, ctx.workspace.id, ctx.user.id, reference_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
