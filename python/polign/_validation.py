"""Small, dependency-free validation helpers shared by both transports."""

from __future__ import annotations

import math
from typing import Any

from . import errors


def validate_filter_finite(value: Any) -> None:
    """Reject NaN and infinities anywhere in a metadata filter.

    JSON has no portable representation for these values, while protobuf does.
    Applying the check before either transport is encoded keeps HTTP and gRPC
    behaviour identical and, importantly, prevents a negated NaN equality from
    becoming a match-all delete.
    """

    if isinstance(value, float):
        if not math.isfinite(value):
            raise errors.InvalidArgumentError(
                "filter operands must be finite numbers"
            )
        return
    if isinstance(value, dict):
        for child in value.values():
            validate_filter_finite(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            validate_filter_finite(child)

