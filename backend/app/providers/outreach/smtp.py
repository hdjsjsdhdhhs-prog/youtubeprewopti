"""SMTP transport (aiosmtplib). Errors are classified for the outreach domain:

* authentication refused (535 …) => ``AUTH_EXPIRED``, not retryable (fix the account);
* recipient refused with 5xx => ``RecipientRejectedError`` (hard bounce);
* 4xx (greylisting, rate limits) and connection / timeout problems => retryable;
* anything else 5xx => not retryable.
"""

from __future__ import annotations

import aiosmtplib

from app.core.errors import IntegrationError, IntegrationErrorCode
from app.providers.outreach.base import (
    EmailTransport,
    OutgoingEmail,
    RecipientRejectedError,
    SendResult,
    SmtpConfig,
    build_mime,
)

PROVIDER = "smtp"


def _client(config: SmtpConfig, timeout: float) -> aiosmtplib.SMTP:
    return aiosmtplib.SMTP(
        hostname=config.host,
        port=config.port,
        use_tls=config.security == "ssl",
        start_tls=config.security == "starttls",
        timeout=timeout,
    )


def classify(exc: Exception) -> IntegrationError:
    if isinstance(exc, aiosmtplib.SMTPAuthenticationError):
        return IntegrationError(
            IntegrationErrorCode.AUTH_EXPIRED,
            f"SMTP-сервер не принял логин или пароль ({exc.code}). Проверьте настройки почтового аккаунта.",
            retryable=False,
            provider=PROVIDER,
            http_status=exc.code,
        )
    if isinstance(exc, aiosmtplib.SMTPRecipientsRefused):
        codes = [e.code for e in exc.recipients]
        code = codes[0] if codes else None
        if code is not None and code < 500:
            return IntegrationError(
                IntegrationErrorCode.RATE_LIMITED,
                f"Сервер временно не принял получателя ({code}).",
                retryable=True,
                provider=PROVIDER,
                http_status=code,
            )
        return RecipientRejectedError(
            f"Сервер отклонил адрес получателя ({code}): адрес не существует или не принимает почту.",
            provider=PROVIDER,
            smtp_code=code,
        )
    if isinstance(exc, aiosmtplib.SMTPRecipientRefused):
        if exc.code >= 500:
            return RecipientRejectedError(
                f"Сервер отклонил адрес получателя ({exc.code}): {exc.message}",
                provider=PROVIDER,
                smtp_code=exc.code,
            )
        return IntegrationError(
            IntegrationErrorCode.RATE_LIMITED,
            f"Сервер временно не принял получателя ({exc.code}).",
            retryable=True,
            provider=PROVIDER,
            http_status=exc.code,
        )
    if isinstance(exc, aiosmtplib.SMTPResponseException):
        retryable = 400 <= exc.code < 500
        return IntegrationError(
            IntegrationErrorCode.RATE_LIMITED if retryable else IntegrationErrorCode.UNKNOWN,
            f"SMTP-сервер ответил {exc.code}: {exc.message}",
            retryable=retryable,
            provider=PROVIDER,
            http_status=exc.code,
        )
    if isinstance(exc, aiosmtplib.SMTPTimeoutError | TimeoutError):
        return IntegrationError(
            IntegrationErrorCode.TIMEOUT, "SMTP-сервер не ответил вовремя.", retryable=True, provider=PROVIDER
        )
    if isinstance(exc, aiosmtplib.SMTPException | OSError):
        return IntegrationError(
            IntegrationErrorCode.PROVIDER_UNAVAILABLE,
            f"Не удалось подключиться к SMTP-серверу ({type(exc).__name__}).",
            retryable=True,
            provider=PROVIDER,
        )
    raise exc


class SmtpTransport:
    name = "smtp"

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    async def verify(self, config: SmtpConfig) -> None:
        client = _client(config, self.timeout)
        try:
            await client.connect()
            if config.username:
                await client.login(config.username, config.password)
            await client.noop()
        except Exception as exc:  # noqa: BLE001 — classified
            raise classify(exc) from exc
        finally:
            if client.is_connected:
                try:
                    await client.quit()
                except aiosmtplib.SMTPException:
                    client.close()

    async def send(self, config: SmtpConfig, email: OutgoingEmail) -> SendResult:
        client = _client(config, self.timeout)
        try:
            await client.connect()
            if config.username:
                await client.login(config.username, config.password)
            await client.send_message(build_mime(email))
        except Exception as exc:  # noqa: BLE001 — classified
            raise classify(exc) from exc
        finally:
            if client.is_connected:
                try:
                    await client.quit()
                except aiosmtplib.SMTPException:
                    client.close()
        return SendResult(provider_message_id=email.message_id)


_check: type[EmailTransport] = SmtpTransport
