from app.core.config import Settings
from app.providers.outreach.base import (
    Attachment,
    EmailTransport,
    OutgoingEmail,
    RecipientRejectedError,
    SendResult,
    SmtpConfig,
    SmtpSecurity,
    build_mime,
)
from app.providers.outreach.mock import MockEmailTransport
from app.providers.outreach.smtp import SmtpTransport


def email_transport(settings: Settings) -> EmailTransport:
    if settings.effective_email_transport == "mock":
        return MockEmailTransport()
    return SmtpTransport(timeout=settings.smtp_timeout_seconds)


__all__ = [
    "Attachment",
    "EmailTransport",
    "MockEmailTransport",
    "OutgoingEmail",
    "RecipientRejectedError",
    "SendResult",
    "SmtpConfig",
    "SmtpSecurity",
    "SmtpTransport",
    "build_mime",
    "email_transport",
]
