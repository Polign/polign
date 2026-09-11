"""Exceptions raised by the polign client.

Every error raised by the SDK subclasses :class:`PolignError`, so callers can
catch one type. Specific subclasses mirror the server's status codes on both
transports (HTTP status / gRPC code).
"""

from __future__ import annotations

from typing import Optional


class PolignError(Exception):
    """Base class for all polign client errors."""


class ConnectionError(PolignError):
    """The HTTP connection could not be reached or failed mid-request."""


class InvalidArgumentError(PolignError):
    """The request was malformed: bad id, dimension mismatch, k <= 0, ...

    HTTP 400 / gRPC INVALID_ARGUMENT.
    """


class NotFoundError(PolignError):
    """The collection or vector does not exist. HTTP 404 / gRPC NOT_FOUND."""


class AuthenticationError(PolignError):
    """Missing or invalid API key. HTTP 401 / gRPC UNAUTHENTICATED."""


class PermissionDeniedError(PolignError):
    """The API key is not permitted to perform this operation.

    HTTP 403 / gRPC PERMISSION_DENIED.
    """


class RateLimitError(PolignError):
    """Server admission capacity was exhausted.

    This covers API-key rate limiting and a full write backlog. HTTP 429 / gRPC
    RESOURCE_EXHAUSTED.
    """


class NotOwnerError(PolignError):
    """In fleet mode, this node does not own the resource.

    HTTP 421 / gRPC FAILED_PRECONDITION. ``owner`` names the owning node when
    the server reports it; reconnect to that node and retry.
    """

    def __init__(self, message: str, owner: Optional[str] = None):
        super().__init__(message)
        self.owner = owner


class ServerError(PolignError):
    """The server failed internally.

    HTTP 5xx other than 503 / gRPC INTERNAL.
    """


class NotEnabledError(PolignError):
    """The operation is not enabled or supported by this server.

    This includes a disabled collections API, and point reads against a
    cold-served collection too large to carry per-segment id filters (smaller
    ones are served from segments). HTTP 501 / gRPC UNIMPLEMENTED.
    """


class ConflictError(PolignError):
    """The collection already exists, or is still pending verification.

    HTTP 409 / gRPC ALREADY_EXISTS, FAILED_PRECONDITION.
    """


class UnavailableError(PolignError):
    """The service or collection backend is temporarily unavailable.

    HTTP 503 / gRPC UNAVAILABLE. gRPC also uses UNAVAILABLE for transport
    outages, which cannot be reliably distinguished from a server status.
    """
