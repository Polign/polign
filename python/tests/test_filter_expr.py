"""Unit tests for the dict → FilterExpr conversion used by the gRPC client.

Mirrors the server-side JSON filter parser (internal/filter/json.go): the
same dicts accepted by the HTTP client must produce the equivalent typed
wire tree. Needs the ``grpc`` extra (protobuf) — skipped without it.
"""

import math

import pytest

pytest.importorskip("google.protobuf")

from polign import InvalidArgumentError  # noqa: E402
from polign._filter import filter_expr_from_dict  # noqa: E402
from polign._pb import vectordb_pb2 as pb  # noqa: E402


def cond(c):
    return pb.FilterExpr(cond=c)


def eq(key, value):
    return cond(pb.FilterCond(key=key, eq=value))


def typed_eq(key, **value):
    return cond(pb.FilterCond(key=key, typed_eq=pb.TypedValue(**value)))


def test_no_filter():
    assert filter_expr_from_dict(None) is None
    assert filter_expr_from_dict({}) is None


def test_bare_equality():
    assert filter_expr_from_dict({"lang": "en"}) == eq("lang", "en")


def test_scalar_forms():
    assert filter_expr_from_dict({"score": 0.5}) == typed_eq("score", number=0.5)
    assert filter_expr_from_dict({"n": 5}) == typed_eq("n", number=5)
    assert filter_expr_from_dict({"flag": True}) == typed_eq("flag", boolean=True)


@pytest.mark.parametrize(
    "bad",
    [
        {"score": math.nan},
        {"score": {"$eq": math.inf}},
        {"score": {"$ne": -math.inf}},
        {"score": {"$in": [1, math.nan]}},
        {"score": {"$gte": math.inf}},
        {"$not": {"score": math.nan}},
    ],
)
def test_non_finite_operands_rejected_client_side(bad):
    with pytest.raises(InvalidArgumentError, match="finite"):
        filter_expr_from_dict(bad)


def test_multi_key_ands_sorted():
    got = filter_expr_from_dict({"b": "2", "a": "1"})
    want = pb.FilterExpr(
        **{"and": pb.FilterJunction(exprs=[eq("a", "1"), eq("b", "2")])}
    )
    assert got == want


def test_in_exists_ne():
    got = filter_expr_from_dict({"tag": {"$in": ["a", "b"]}})
    assert got == cond(pb.FilterCond(key="tag", **{"in": pb.ValueList(values=["a", "b"])}))

    got = filter_expr_from_dict({"tag": {"$exists": False}})
    assert got == cond(pb.FilterCond(key="tag", exists=False))

    got = filter_expr_from_dict({"tag": {"$ne": "x"}})
    assert got == pb.FilterExpr(**{"not": eq("tag", "x")})

    got = filter_expr_from_dict({"value": {"$in": [1, True, "one"]}})
    assert got == cond(
        pb.FilterCond(
            key="value",
            typed_in=pb.TypedValueList(
                values=[
                    pb.TypedValue(number=1),
                    pb.TypedValue(boolean=True),
                    pb.TypedValue(str="one"),
                ]
            ),
        )
    )


def test_range_merges_bounds():
    got = filter_expr_from_dict({"score": {"$gt": 0.5, "$lte": 2}})
    want = cond(
        pb.FilterCond(
            key="score", range=pb.FilterRange(gt="0.5", lte="2", numeric=True)
        )
    )
    assert got == want

    got = filter_expr_from_dict({"ts": {"$gte": "2026-01-01"}})
    want = cond(
        pb.FilterCond(key="ts", range=pb.FilterRange(gte="2026-01-01", numeric=False))
    )
    assert got == want


def test_ops_under_one_key_and_together():
    got = filter_expr_from_dict({"score": {"$exists": True, "$gt": 1}})
    want = pb.FilterExpr(
        **{
            "and": pb.FilterJunction(
                exprs=[
                    cond(pb.FilterCond(key="score", exists=True)),
                    cond(pb.FilterCond(key="score", range=pb.FilterRange(gt="1", numeric=True))),
                ]
            )
        }
    )
    assert got == want


def test_composers():
    got = filter_expr_from_dict({"$or": [{"lang": "en"}, {"lang": {"$exists": False}}]})
    want = pb.FilterExpr(
        **{
            "or": pb.FilterJunction(
                exprs=[eq("lang", "en"), cond(pb.FilterCond(key="lang", exists=False))]
            )
        }
    )
    assert got == want

    got = filter_expr_from_dict({"$not": {"lang": "en"}})
    assert got == pb.FilterExpr(**{"not": eq("lang", "en")})

    got = filter_expr_from_dict({"$and": [{"a": "1"}, {"b": "2"}]})
    want = pb.FilterExpr(
        **{"and": pb.FilterJunction(exprs=[eq("a", "1"), eq("b", "2")])}
    )
    assert got == want


@pytest.mark.parametrize(
    "bad",
    [
        {"$bogus": "x"},
        {"key": {"$bogus": "x"}},
        {"key": {}},
        {"key": {"$in": "not-a-list"}},
        {"key": {"$exists": "yes"}},
        {"key": {"$gt": 1, "$lt": "z"}},  # mixed numeric/lexicographic bounds
        {"key": {"$gt": True}},
        {"key": ["no", "arrays"]},
        {"$not": "not-an-object"},
        {"$not": {}},
        {"$and": []},
        {"$or": [{"a": "1"}, "nope"]},
        {"$or": [{}]},
    ],
)
def test_malformed_filters_raise(bad):
    with pytest.raises(InvalidArgumentError):
        filter_expr_from_dict(bad)
