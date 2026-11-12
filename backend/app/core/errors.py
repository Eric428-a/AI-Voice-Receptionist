"""Shared exception types used across services."""

from __future__ import annotations


class HaumexError(Exception):
    """Base class for application errors."""

    code: str = "internal_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class ProviderNotConfiguredError(HaumexError):
    """Raised when an external provider is used without the required configuration."""

    code = "provider_not_configured"


class ProviderError(HaumexError):
    """Raised when an external provider call fails."""

    code = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message, code=code)
        self.status_code = status_code
        self.retryable = retryable
