"""Offline email transport: nothing leaves the machine. Records every message (demo mode, tests); a script of
exceptions can be given to simulate server errors."""

from __future__ import annotations

from app.providers.outreach.base import EmailTransport, OutgoingEmail, SendResult, SmtpConfig, build_mime


class MockEmailTransport:
    name = "mock"

    def __init__(self, *script: BaseException | None) -> None:
        self.script = list(script)
        self.sent: list[OutgoingEmail] = []
        self.verified = 0

    async def verify(self, config: SmtpConfig) -> None:
        self.verified += 1
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise step

    async def send(self, config: SmtpConfig, email: OutgoingEmail) -> SendResult:
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise step
        build_mime(email)  # the message must be buildable exactly as SMTP would send it
        self.sent.append(email)
        return SendResult(provider_message_id=email.message_id)


_check: type[EmailTransport] = MockEmailTransport
