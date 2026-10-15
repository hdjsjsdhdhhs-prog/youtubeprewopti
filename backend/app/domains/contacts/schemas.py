from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.domains.contacts.models import ContactSource, ContactStatus, ContactType, SuppressionReason
from app.domains.contacts.scoring import ContactabilityWeights
from app.domains.jobs.schemas import JobRunOut
from app.domains.leads.results import RescoreResult

Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Value = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=320)]


class ContactOut(BaseModel):
    id: int
    type: ContactType
    value: str
    value_normalized: str
    source: ContactSource
    source_url: str | None
    source_video_id: int | None
    seen_in_videos: int
    confidence: Decimal
    status: ContactStatus  # effective for the workspace: its own verdict, else the contact's
    present: bool  # False = no longer in the channel's texts
    suppressed: bool  # the value is in the workspace suppression list
    usable: bool  # counts for Contactability (present, not invalid / opted out / suppressed)
    is_manual: bool  # added by an operator of this workspace
    note: str
    discovered_at: datetime
    last_seen_at: datetime
    verified_at: datetime | None


class ContactScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    extractor_version: int
    texts_scanned: int
    contacts_found: int
    scanned_at: datetime


class ChannelContacts(BaseModel):
    channel_id: int
    status: Literal["not_scanned", "found", "not_found"]  # ТЗ §18: not_found = scanned, nothing usable
    scan: ContactScanOut | None
    contactability_score: Decimal | None  # ТЗ §19, workspace weights; None = not scanned
    usable_types: list[ContactType]
    items: list[ContactOut]  # usable first, then by type and confidence


class ManualContactIn(BaseModel):
    """``value`` is normalised by type (``@user`` / ``t.me/user`` for Telegram, ``vk.com/id`` for VK …);
    a value the type does not accept is 422 ``invalid_contact``."""

    model_config = ConfigDict(extra="forbid")
    type: ContactType
    value: Value
    status: Literal["verified", "unverified"] = "unverified"
    note: Note = ""


class ContactStatusIn(BaseModel):
    """``opted_out`` also adds the value to the suppression list; it is final for this endpoint (remove the
    suppression entry to undo, admin)."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["public", "verified", "unverified", "invalid", "opted_out"]
    note: Note = ""


class ContactChange(BaseModel):
    contacts: ChannelContacts
    rescored: RescoreResult


class SuppressionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: ContactType
    value: Value
    reason: Literal["opt_out", "manual", "complaint", "bounce"] = "manual"
    note: Note = ""


class SuppressionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    type: ContactType
    value_normalized: str
    reason: SuppressionReason
    note: str
    created_by: int | None
    created_at: datetime


class SuppressionList(BaseModel):
    items: list[SuppressionOut]
    total: int
    limit: int
    offset: int


class ContactScanStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rescan: bool = False  # also channels already scanned with the current extractor


class ContactScanStartResult(BaseModel):
    job: JobRunOut | None  # None = nothing to scan
    created: bool
    channels: int
    remaining: int  # channels left for a further run (per-job limit)


class ContactStats(BaseModel):
    """Contacts of a project's channels (workspace view)."""

    channels: int
    scanned: int
    with_contacts: int
    not_found: int
    by_type: dict[str, int]  # channels with a usable contact of the type


class ContactabilityProfileOut(BaseModel):
    version: int  # 0 = built-in defaults
    is_default: bool
    weights: ContactabilityWeights


class ContactabilityProfileSaved(ContactabilityProfileOut):
    rescored: RescoreResult
