from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, model_validator

from app.domains.offers.models import OutreachChannel
from app.domains.outreach.models import (
    DEFAULT_COOLDOWN_MAX,
    DEFAULT_COOLDOWN_MIN,
    DEFAULT_DAILY_LIMIT,
    DEFAULT_OPT_OUT_TEXT,
    MAX_DAILY_LIMIT,
    AccountStatus,
    MessageEventType,
    MessageStatus,
    SmtpSecurityMode,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Host = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
OptOut = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


# ---------------------------------------------------------------------------
# Email accounts
# ---------------------------------------------------------------------------
class EmailAccountIn(BaseModel):
    """A new SMTP account. The password goes to the encrypted secret store and is never returned."""

    model_config = ConfigDict(extra="forbid")
    name: Name
    from_email: EmailStr
    from_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    reply_to: EmailStr | None = None
    smtp_host: Host
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: SmtpSecurityMode = SmtpSecurityMode.STARTTLS
    smtp_username: Annotated[str, StringConstraints(strip_whitespace=True, max_length=320)] = ""
    password: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None
    daily_limit: int = Field(default=DEFAULT_DAILY_LIMIT, ge=1, le=MAX_DAILY_LIMIT)
    cooldown_min_seconds: int = Field(default=DEFAULT_COOLDOWN_MIN, ge=0, le=86_400)
    cooldown_max_seconds: int = Field(default=DEFAULT_COOLDOWN_MAX, ge=0, le=86_400)
    opt_out_text: OptOut = DEFAULT_OPT_OUT_TEXT

    @model_validator(mode="after")
    def _cooldown(self) -> EmailAccountIn:
        if self.cooldown_max_seconds < self.cooldown_min_seconds:
            raise ValueError("cooldown_max_seconds must be ≥ cooldown_min_seconds")
        return self


class EmailAccountPatch(BaseModel):
    """Only the fields sent change; ``password`` replaces the stored one."""

    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    from_email: EmailStr | None = None
    from_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    reply_to: EmailStr | None = None
    smtp_host: Host | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: SmtpSecurityMode | None = None
    smtp_username: Annotated[str, StringConstraints(strip_whitespace=True, max_length=320)] | None = None
    password: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=MAX_DAILY_LIMIT)
    cooldown_min_seconds: int | None = Field(default=None, ge=0, le=86_400)
    cooldown_max_seconds: int | None = Field(default=None, ge=0, le=86_400)
    opt_out_text: OptOut | None = None


class EmailAccountOut(BaseModel):
    id: int
    name: str
    from_email: str
    from_name: str
    reply_to: str | None
    smtp_host: str
    smtp_port: int
    smtp_security: SmtpSecurityMode
    smtp_username: str
    password_configured: bool
    password_hint: str | None  # «••••••••wxyz», never the password
    status: AccountStatus
    paused_reason: str | None
    daily_limit: int
    sent_today: int  # UTC day
    remaining_today: int
    cooldown_min_seconds: int
    cooldown_max_seconds: int
    next_send_at: datetime | None
    opt_out_text: str
    consecutive_failures: int
    bounces: int
    replies: int
    opt_outs: int
    last_sent_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    last_checked_at: datetime | None
    created_at: datetime


class AccountCheck(BaseModel):
    ok: bool
    transport: str  # smtp | mock
    error_code: str | None
    message: str


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
class SendIn(BaseModel):
    """«Send now»: echo ``content_hash`` of the approved offer on screen. ``email_account_id`` — the sender
    for the email channel (default: the offer's, else the first active account)."""

    model_config = ConfigDict(extra="forbid")
    content_hash: Sha256
    email_account_id: int | None = None


class MessageEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["delivered", "opened", "replied", "positive_reply", "negative_reply", "bounced", "opt_out"]
    note: Note = ""


class MarkSentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: Note = ""


class MessageEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    type: MessageEventType
    note: str
    payload: dict[str, object]
    user_id: int | None
    occurred_at: datetime


class MessageOut(BaseModel):
    id: int
    lead_id: int
    offer_id: int
    channel_id: int
    channel_title: str
    channel: OutreachChannel
    status: MessageStatus
    email_account_id: int | None
    email_account_name: str | None
    contact_id: int | None
    contact_type: str
    to_address: str
    subject: str
    body: str  # exactly what is (or was) sent, opt-out footer included
    attachment_url: str | None
    content_hash: str
    attempts: int
    scheduled_at: datetime
    next_attempt_at: datetime | None
    sent_at: datetime | None
    provider_message_id: str | None
    error_code: str | None
    error_message: str | None
    events: list[MessageEventOut]
    created_at: datetime


class MessageList(BaseModel):
    items: list[MessageOut]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]  # messages per status in the workspace
