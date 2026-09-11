"""polign — Python client for polign_db.

HTTP client (zero dependencies):

    from polign import Client
    c = Client("http://localhost:23000")

gRPC client (pip install polign[grpc]):

    from polign import GrpcClient
    c = GrpcClient("localhost:23001")

Both expose the same data-plane operations, including batch/filter deletion
and collection description for framework integrations.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

from .client import Client
from .errors import (
    AuthenticationError,
    ConflictError,
    ConnectionError,
    InvalidArgumentError,
    NotEnabledError,
    NotFoundError,
    NotOwnerError,
    PermissionDeniedError,
    PolignError,
    RateLimitError,
    ServerError,
    UnavailableError,
)
from .types import (
    CollectionBackend,
    CollectionDescription,
    DeleteResult,
    CollectionInfo,
    Fusion,
    Hit,
    Vector,
    VectorPage,
)

# The installed distribution's version is the source of truth (it comes from
# python/pyproject.toml, which the publish workflow checks against the
# tag). The literal below only serves a source checkout that was never
# installed and is kept in step by the same workflow check.
try:
    __version__ = _dist_version("polign")
except PackageNotFoundError:  # running from a source tree, not installed
    __version__ = "0.7.0"

__all__ = [
    "Client",
    "GrpcClient",
    "Vector",
    "Hit",
    "Fusion",
    "VectorPage",
    "PolignError",
    "ConnectionError",
    "InvalidArgumentError",
    "NotFoundError",
    "AuthenticationError",
    "PermissionDeniedError",
    "RateLimitError",
    "NotOwnerError",
    "ServerError",
    "ConflictError",
    "NotEnabledError",
    "UnavailableError",
    "CollectionBackend",
    "CollectionDescription",
    "DeleteResult",
    "CollectionInfo",
    "__version__",
]


def __getattr__(name):
    # Lazy import so the base package works without grpcio installed.
    if name == "GrpcClient":
        from .grpc_client import GrpcClient

        return GrpcClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
