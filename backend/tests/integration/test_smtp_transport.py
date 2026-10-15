"""Phase 8.1 SMTP transport against a real local SMTP server (aiosmtpd on 127.0.0.1, no network): the message
as received (headers, text, attachment), a refused recipient (hard bounce), a refused login, an unreachable
server, and ``verify`` (connect + login, nothing sent)."""

from __future__ import annotations

import email
import socket
from email import policy

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.providers.outreach import (
    Attachment,
    OutgoingEmail,
    RecipientRejectedError,
    SmtpConfig,
    SmtpTransport,
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Recorder:
    def __init__(self) -> None:
        self.messages: list[tuple[str, list[str], bytes]] = []

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: N802
        if address.startswith("nobody@"):
            return "550 5.1.1 No such user"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos), envelope.content))
        return "250 Message accepted"


def _authenticator(server, session, envelope, mechanism, auth_data):
    ok = isinstance(auth_data, LoginPassword) and auth_data.password == b"secret"
    # handled=False: let aiosmtpd answer 535 itself (the default handled=True means «already answered»)
    return AuthResult(success=ok, handled=False)


@pytest.fixture
def smtp_server():
    recorder = Recorder()
    port = _free_port()
    controller = Controller(
        recorder,
        hostname="127.0.0.1",
        port=port,
        authenticator=_authenticator,
        auth_require_tls=False,
    )
    controller.start()
    try:
        yield recorder, port
    finally:
        controller.stop()


def _email(to: str = "author@example.org") -> OutgoingEmail:
    return OutgoingEmail(
        from_email="me@example.com",
        from_name="Петя Дизайнер",
        to=to,
        subject="Превью для «Кухни»",
        text="Здравствуйте!\n\n—\nЕсли не хотите писем, ответьте «нет».",
        message_id="<ytl-1-abc@example.com>",
        reply_to="reply@example.com",
        attachments=(Attachment("preview.jpeg", b"\xff\xd8fakejpeg", "image/jpeg"),),
        list_unsubscribe="mailto:reply@example.com?subject=unsubscribe",
    )


def _config(port: int, *, username: str = "me", password: str = "secret") -> SmtpConfig:  # noqa: S107 — the local test server's password
    return SmtpConfig(host="127.0.0.1", port=port, security="none", username=username, password=password)


async def test_send_delivers_exactly_the_built_message(smtp_server):
    recorder, port = smtp_server
    result = await SmtpTransport(timeout=5).send(_config(port), _email())
    assert result.provider_message_id == "<ytl-1-abc@example.com>"
    [(mail_from, rcpts, raw)] = recorder.messages
    assert mail_from == "me@example.com" and rcpts == ["author@example.org"]
    msg = email.message_from_bytes(raw, policy=policy.default)
    assert msg["Subject"] == "Превью для «Кухни»" and msg["Reply-To"] == "reply@example.com"
    assert msg["List-Unsubscribe"] == "<mailto:reply@example.com?subject=unsubscribe>"
    assert msg["Message-ID"] == "<ytl-1-abc@example.com>" and "Петя Дизайнер" in str(msg["From"])
    body = msg.get_body(preferencelist=("plain",))
    assert body is not None and "ответьте «нет»" in body.get_content()
    [att] = list(msg.iter_attachments())
    assert att.get_filename() == "preview.jpeg" and att.get_content() == b"\xff\xd8fakejpeg"


async def test_refused_recipient_is_a_hard_bounce(smtp_server):
    recorder, port = smtp_server
    with pytest.raises(RecipientRejectedError) as exc:
        await SmtpTransport(timeout=5).send(_config(port), _email("nobody@example.org"))
    assert exc.value.http_status == 550 and not exc.value.retryable
    assert recorder.messages == []


async def test_refused_login_is_not_retryable(smtp_server):
    _, port = smtp_server
    with pytest.raises(IntegrationError) as exc:
        await SmtpTransport(timeout=5).send(_config(port, password="wrong"), _email())
    assert exc.value.code == IntegrationErrorCode.AUTH_EXPIRED and not exc.value.retryable


async def test_verify_logs_in_without_sending(smtp_server):
    recorder, port = smtp_server
    await SmtpTransport(timeout=5).verify(_config(port))
    assert recorder.messages == []
    with pytest.raises(IntegrationError):
        await SmtpTransport(timeout=5).verify(_config(port, password="wrong"))


async def test_unreachable_server_is_retryable():
    port = _free_port()  # nothing listens there
    with pytest.raises(IntegrationError) as exc:
        await SmtpTransport(timeout=3).send(_config(port), _email())
    assert exc.value.retryable and exc.value.code in (
        IntegrationErrorCode.PROVIDER_UNAVAILABLE,
        IntegrationErrorCode.TIMEOUT,
    )
