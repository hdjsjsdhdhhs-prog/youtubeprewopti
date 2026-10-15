"""Email transport protocol (ADR-0010).

Transports only deliver a fully built message to the server and classify errors; every guardrail (approval,
suppression list, contact status, quotas, cooldown, idempotency) lives in ``app.domains.outreach`` so a
transport cannot be «forgotten» to check something.

SMTP gives no delivery / open / reply information: those events are recorded by the operator (Phase 8.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from typing import Literal, Protocol

from app.core.errors import IntegrationError, IntegrationErrorCode

SmtpSecurity = Literal["starttls", "ssl", "none"]


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: SmtpSecurity
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class Attachment:
    filename: str
    data: bytes
    mime: str  # e.g. "image/jpeg"


@dataclass(frozen=True)
class OutgoingEmail:
    from_email: str
    from_name: str
    to: str
    subject: str
    text: str
    message_id: str  # "<…@domain>", stored on the message (provider_message_id)
    reply_to: str | None = None
    attachments: tuple[Attachment, ...] = ()
    list_unsubscribe: str | None = None  # mailto: link (RFC 2369)


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str


class RecipientRejectedError(IntegrationError):
    """The server refused the recipient (5xx on RCPT): a hard bounce — the address goes to the suppression
    list and the message is not retried."""

    def __init__(self, human_message: str, *, provider: str, smtp_code: int | None) -> None:
        super().__init__(
            IntegrationErrorCode.INVALID_INPUT,
            human_message,
            retryable=False,
            provider=provider,
            http_status=smtp_code,
        )


class EmailTransport(Protocol):
    name: str  # "smtp" | "mock"

    async def send(self, config: SmtpConfig, email: OutgoingEmail) -> SendResult: ...

    async def verify(self, config: SmtpConfig) -> None:
        """Connect and log in without sending anything; raises a classified ``IntegrationError``."""
        ...


def build_mime(email: OutgoingEmail) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((email.from_name, email.from_email)) if email.from_name else email.from_email
    msg["To"] = email.to
    msg["Subject"] = email.subject
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    msg["Message-ID"] = email.message_id
    if email.reply_to:
        msg["Reply-To"] = email.reply_to
    if email.list_unsubscribe:
        msg["List-Unsubscribe"] = f"<{email.list_unsubscribe}>"
    msg.set_content(email.text)
    for a in email.attachments:
        maintype, _, subtype = a.mime.partition("/")
        msg.add_attachment(a.data, maintype=maintype, subtype=subtype or "octet-stream", filename=a.filename)
    return msg
