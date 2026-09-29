"""Domain and integration errors with human-readable messages (§60)."""

from __future__ import annotations

from enum import StrEnum


class AppError(Exception):
    """Base for errors that map to an HTTP response."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class InvalidTransitionError(ConflictError):
    code = "invalid_transition"


class IntegrationErrorCode(StrEnum):
    AUTH_EXPIRED = "auth_expired"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_INPUT = "invalid_input"
    TIMEOUT = "timeout"
    NOT_CONFIGURED = "not_configured"
    UNKNOWN = "unknown"


class IntegrationError(Exception):
    """Error from an external provider, classified for retry decisions and UI display."""

    def __init__(
        self,
        code: IntegrationErrorCode,
        human_message: str,
        *,
        retryable: bool,
        provider: str,
        http_status: int | None = None,
    ) -> None:
        super().__init__(f"[{provider}:{code}] {human_message}")
        self.code = code
        self.human_message = human_message
        self.retryable = retryable
        self.provider = provider
        self.http_status = http_status


class RetryableJobError(Exception):
    """Raised inside job handlers to request a retry via the queue."""


def classify_http_status(provider: str, status: int, context: str) -> IntegrationError:
    """Map an HTTP status of an external call to a classified error."""
    if status in (401,):
        return IntegrationError(
            IntegrationErrorCode.AUTH_EXPIRED,
            f"{provider}: credentials are invalid or expired while {context}. Re-authentication required.",
            retryable=False, provider=provider, http_status=status,
        )
    if status == 403:
        return IntegrationError(
            IntegrationErrorCode.FORBIDDEN, f"{provider}: access forbidden while {context}.",
            retryable=False, provider=provider, http_status=status,
        )
    if status == 404:
        return IntegrationError(
            IntegrationErrorCode.NOT_FOUND, f"{provider}: resource not found while {context}.",
            retryable=False, provider=provider, http_status=status,
        )
    if status == 429:
        return IntegrationError(
            IntegrationErrorCode.RATE_LIMITED,
            f"{provider}: rate limit hit while {context}; will retry later.",
            retryable=True, provider=provider, http_status=status,
        )
    if status >= 500:
        return IntegrationError(
            IntegrationErrorCode.PROVIDER_UNAVAILABLE,
            f"{provider}: service temporarily unavailable (HTTP {status}) while {context}; will retry.",
            retryable=True, provider=provider, http_status=status,
        )
    return IntegrationError(
        IntegrationErrorCode.UNKNOWN, f"{provider}: unexpected HTTP {status} while {context}.",
        retryable=False, provider=provider, http_status=status,
    )
