"""Converts the dict filter language into the wire FilterExpr tree.

The gRPC client accepts the same metadata-filter dicts as the HTTP client
and converts them client-side into the
``polign.v1.FilterExpr`` proto. The semantics mirror the server's JSON parser
(internal/filter/json.go): bare values are equality, per-key operator objects
($eq, $ne, $in, $gt, $gte, $lt, $lte, $exists) AND together with the four
range operators merged into one range, and $and/$or/$not compose sub-filters.

Only imported by the gRPC transport — needs the ``grpc`` extra for protobuf.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from . import errors
from ._pb import vectordb_pb2 as pb
from ._validation import validate_filter_finite

_RANGE_OPS = {"$gt": "gt", "$gte": "gte", "$lt": "lt", "$lte": "lte"}


def filter_expr_from_dict(obj: Optional[Dict[str, Any]]) -> Optional["pb.FilterExpr"]:
    """Convert a filter dict to a FilterExpr, or None for no filter.

    Raises :class:`polign.InvalidArgumentError` on a malformed filter, with
    the same messages the server would produce.
    """
    if not obj:
        return None
    if not isinstance(obj, dict):
        raise errors.InvalidArgumentError("filter: not a JSON object")
    validate_filter_finite(obj)
    return _parse_object(obj)


def _parse_object(obj: Dict[str, Any]) -> Optional["pb.FilterExpr"]:
    exprs: List[pb.FilterExpr] = []
    for key in sorted(obj):
        val = obj[key]
        if key in ("$and", "$or"):
            children = _parse_object_list(key, val)
            junction = pb.FilterJunction(exprs=children)
            if key == "$and":
                exprs.append(pb.FilterExpr(**{"and": junction}))
            else:
                exprs.append(pb.FilterExpr(**{"or": junction}))
        elif key == "$not":
            if not isinstance(val, dict):
                raise errors.InvalidArgumentError("filter: $not takes a filter object")
            child = _parse_object(val)
            if child is None:
                raise errors.InvalidArgumentError(
                    "filter: $not takes a non-empty filter object"
                )
            exprs.append(pb.FilterExpr(**{"not": child}))
        elif key.startswith("$"):
            raise errors.InvalidArgumentError(f"filter: unknown operator {key!r}")
        else:
            exprs.append(_parse_field(key, val))
    return _and_collapse(exprs)


def _parse_object_list(op: str, val: Any) -> List["pb.FilterExpr"]:
    if not isinstance(val, list) or not val:
        raise errors.InvalidArgumentError(
            f"filter: {op} takes a non-empty array of filter objects"
        )
    children = []
    for item in val:
        if not isinstance(item, dict):
            raise errors.InvalidArgumentError(
                f"filter: {op} takes a non-empty array of filter objects"
            )
        child = _parse_object(item)
        if child is None:
            raise errors.InvalidArgumentError(f"filter: {op}: empty filter object")
        children.append(child)
    return children


def _parse_field(key: str, val: Any) -> "pb.FilterExpr":
    if isinstance(val, dict):
        return _parse_field_ops(key, val)
    return _equality(key, val)


def _parse_field_ops(key: str, ops: Dict[str, Any]) -> "pb.FilterExpr":
    if not ops:
        raise errors.InvalidArgumentError(f"filter: key {key!r}: empty operator object")
    exprs: List[pb.FilterExpr] = []
    bounds: Dict[str, str] = {}
    numeric_set = lex_set = False
    for op in sorted(ops):
        val = ops[op]
        if op == "$eq":
            exprs.append(_equality(key, val))
        elif op == "$ne":
            eq = _equality(key, val)
            exprs.append(pb.FilterExpr(**{"not": eq}))
        elif op == "$in":
            if not isinstance(val, list):
                raise errors.InvalidArgumentError(f"filter: key {key!r}: $in takes an array")
            if all(isinstance(item, str) for item in val):
                exprs.append(
                    _cond(
                        pb.FilterCond(
                            key=key, **{"in": pb.ValueList(values=val)}
                        )
                    )
                )
            else:
                values = [_typed_scalar(key, item) for item in val]
                exprs.append(
                    _cond(
                        pb.FilterCond(
                            key=key,
                            typed_in=pb.TypedValueList(values=values),
                        )
                    )
                )
        elif op in _RANGE_OPS:
            if isinstance(val, str):
                lex_set = True
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                numeric_set = True
            else:
                raise errors.InvalidArgumentError(
                    f"filter: key {key!r}: {op} takes a string or number"
                )
            bounds[_RANGE_OPS[op]] = val if isinstance(val, str) else str(val)
        elif op == "$exists":
            if not isinstance(val, bool):
                raise errors.InvalidArgumentError(
                    f"filter: key {key!r}: $exists takes a boolean"
                )
            exprs.append(_cond(pb.FilterCond(key=key, exists=val)))
        else:
            raise errors.InvalidArgumentError(
                f"filter: key {key!r}: unknown operator {op!r}"
            )
    if bounds:
        if numeric_set and lex_set:
            raise errors.InvalidArgumentError(
                f"filter: key {key!r}: range bounds mix numbers and strings"
            )
        rng = pb.FilterRange(numeric=numeric_set, **bounds)
        exprs.append(_cond(pb.FilterCond(key=key, range=rng)))
    collapsed = _and_collapse(exprs)
    assert collapsed is not None  # ops was non-empty
    return collapsed


def _and_collapse(exprs: List["pb.FilterExpr"]) -> Optional["pb.FilterExpr"]:
    if not exprs:
        return None
    if len(exprs) == 1:
        return exprs[0]
    return pb.FilterExpr(**{"and": pb.FilterJunction(exprs=exprs)})


def _cond(c: "pb.FilterCond") -> "pb.FilterExpr":
    return pb.FilterExpr(cond=c)


def _equality(key: str, val: Any) -> "pb.FilterExpr":
    # Strings use the string equality field. Numbers and booleans use the typed
    # field so gRPC semantics match HTTP JSON filtering.
    if isinstance(val, str):
        return _cond(pb.FilterCond(key=key, eq=val))
    return _cond(pb.FilterCond(key=key, typed_eq=_typed_scalar(key, val)))


def _typed_scalar(key: str, val: Any) -> "pb.TypedValue":
    if isinstance(val, bool):
        return pb.TypedValue(boolean=val)
    if isinstance(val, str):
        return pb.TypedValue(str=val)
    if isinstance(val, (int, float)):
        try:
            number = float(val)
        except (OverflowError, ValueError):
            raise errors.InvalidArgumentError(
                f"filter: key {key!r}: number is outside float64 range"
            ) from None
        if not math.isfinite(number):
            raise errors.InvalidArgumentError(
                f"filter: key {key!r}: operands must be finite numbers"
            )
        return pb.TypedValue(number=number)
    raise errors.InvalidArgumentError(
        f"filter: key {key!r}: expected a string, number or boolean"
    )


def _scalar(key: str, val: Any) -> str:
    """A scalar's metadata string form: strings as-is, numbers by their
    literal ("0.5"), booleans as "true"/"false" — matching what the HTTP
    client's JSON encoding would send."""
    if isinstance(val, bool):  # before int: bool subclasses int
        return "true" if val else "false"
    if isinstance(val, str):
        return val
    if isinstance(val, (int, float)):
        return str(val)
    raise errors.InvalidArgumentError(
        f"filter: key {key!r}: expected a string, number or boolean"
    )
