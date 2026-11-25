"""Transport-level errors raised by the service layer.

Services know only HTTP; the LiveKit adapters translate these errors into
``APIConnectionError`` / ``APITimeoutError`` / ``APIStatusError``.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for external-service failures."""

    def __init__(self, message: str, *, service: str) -> None:
        super().__init__(message)
        self.service = service


class ServiceTimeout(ServiceError):
    """The service did not answer in time."""


class ServiceUnavailable(ServiceError):
    """The service could not be reached (connection failed)."""


class ServiceResponseError(ServiceError):
    """The service answered with a non-200 status."""

    def __init__(
        self,
        message: str,
        *,
        service: str,
        status_code: int,
        body: str,
    ) -> None:
        super().__init__(message, service=service)
        self.status_code = status_code
        self.body = body
