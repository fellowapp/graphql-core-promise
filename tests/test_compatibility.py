"""Contracts that must survive sharing promise helpers across both adapters."""

import asyncio

import pytest
from graphql import (
    GraphQLField,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
    GraphQLUnionType,
    execute,
    parse,
    version_info,
)
from promise import Promise

from graphql_core_promise import PromiseExecutionContext

IS_33 = version_info >= (3, 3)
EXECUTION_OPTIONS = {
    "executor_class" if IS_33 else "execution_context_class": PromiseExecutionContext
}


@pytest.mark.parametrize(
    "failure",
    ["completion", "list_reject", "list_completion", "non_null", "non_null_none"],
)
def test_errors_are_located_at_the_failing_field(failure):
    def resolve(*_):
        if failure == "list_reject":
            return ["ok", Promise.reject(ValueError("failed"))]
        if failure == "list_completion":
            return ["ok", Promise.resolve({})]
        if failure == "non_null":
            return Promise.reject(ValueError("failed"))
        if failure == "non_null_none":
            return Promise.resolve(None)
        return Promise.resolve({})

    field_type = GraphQLString
    if failure.startswith("list_"):
        field_type = GraphQLList(GraphQLString)
    elif failure.startswith("non_null"):
        field_type = GraphQLNonNull(GraphQLString)
    schema = GraphQLSchema(
        GraphQLObjectType(
            "Query",
            {
                "value": GraphQLField(field_type, resolve=resolve),
                "sibling": GraphQLField(GraphQLString, resolve=lambda *_: "ok"),
            },
        )
    )
    result = execute(schema, parse("{ value sibling }"), **EXECUTION_OPTIONS)

    if failure.startswith("non_null"):
        assert result.data is None
    elif failure.startswith("list_"):
        assert result.data == {"value": ["ok", None], "sibling": "ok"}
    else:
        assert result.data == {"value": None, "sibling": "ok"}

    assert len(result.errors) == 1
    error = result.errors[0]
    assert error.path == (["value", 1] if failure.startswith("list_") else ["value"])
    assert [(location.line, location.column) for location in error.locations] == [
        (1, 3)
    ]
    if failure in {"list_reject", "non_null"}:
        assert error.message == "failed"
    elif failure == "non_null_none":
        assert error.message == "Cannot return null for non-nullable field Query.value."
    else:
        assert error.message == "String cannot represent value: {}"


def _promise_values():
    return Promise.resolve, Promise.reject


def _coroutine_values():
    # Suspend before settling, as real asynchronous resolvers do.
    async def resolved(value):
        await asyncio.sleep(0)
        return value

    async def rejected(error):
        await asyncio.sleep(0)
        raise error

    return resolved, rejected


PARITY_CASES = {
    "promised value fails completion": (lambda ok, _: ok({}), "value"),
    "promised value rejects": (lambda _, fail: fail(ValueError("x")), "value"),
    "non-null resolves None": (lambda ok, _: ok(None), "strict"),
    "non-null rejects": (lambda _, fail: fail(ValueError("x")), "strict"),
    "list item rejects": (lambda _, fail: ["ok", fail(ValueError("x"))], "items"),
    "list item fails completion": (lambda ok, _: ["ok", ok({})], "items"),
    "non-null list item rejects": (
        lambda _, fail: ["ok", fail(ValueError("x"))],
        "strictItems",
    ),
    "promised list, item rejects": (
        lambda ok, fail: ok(["ok", fail(ValueError("x"))]),
        "items",
    ),
    "promised object, non-null child rejects": (
        lambda ok, _: ok({}),
        "child { name strict }",
    ),
    "list of objects, child rejects": (
        lambda ok, _: [ok({}), {}],
        "children { name strict }",
    ),
}


@pytest.mark.parametrize("case", list(PARITY_CASES))
def test_errors_match_graphql_core_async_execution(case):
    """Promise results must complete exactly like graphql-core's awaitables."""
    make_value, selection = PARITY_CASES[case]

    def run(values, **options):
        ok, fail = values()

        def resolve_value(*_):
            return make_value(ok, fail)

        child = GraphQLObjectType(
            "Child",
            {
                "name": GraphQLField(GraphQLString, resolve=lambda *_: "a"),
                "strict": GraphQLField(
                    GraphQLNonNull(GraphQLString),
                    resolve=lambda *_: fail(ValueError("child failed")),
                ),
            },
        )
        fields = {
            "value": GraphQLField(GraphQLString, resolve=resolve_value),
            "strict": GraphQLField(
                GraphQLNonNull(GraphQLString), resolve=resolve_value
            ),
            "items": GraphQLField(GraphQLList(GraphQLString), resolve=resolve_value),
            "strictItems": GraphQLField(
                GraphQLList(GraphQLNonNull(GraphQLString)), resolve=resolve_value
            ),
            "child": GraphQLField(child, resolve=resolve_value),
            "children": GraphQLField(GraphQLList(child), resolve=resolve_value),
            "sibling": GraphQLField(GraphQLString, resolve=lambda *_: "ok"),
        }
        schema = GraphQLSchema(GraphQLObjectType("Query", fields))
        result = execute(schema, parse(f"{{ {selection} sibling }}"), **options)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        errors = sorted(
            (
                error.message,
                error.path or [],
                [(loc.line, loc.column) for loc in error.locations or []],
            )
            for error in result.errors or []
        )
        return result.data, errors

    assert run(_promise_values, **EXECUTION_OPTIONS) == run(_coroutine_values)


@pytest.mark.parametrize("promised_indices", [(), (2,), (0, 2)])
def test_collects_mixed_results_without_changing_order(promised_indices):
    values = ["first", "middle", "last"]
    items = [
        Promise.resolve(value) if index in promised_indices else value
        for index, value in enumerate(values)
    ]
    schema = GraphQLSchema(
        GraphQLObjectType(
            "Query",
            {
                "first": GraphQLField(GraphQLString, resolve=lambda *_: "sync"),
                "items": GraphQLField(
                    GraphQLList(GraphQLString), resolve=lambda *_: items
                ),
                "last": GraphQLField(
                    GraphQLString, resolve=lambda *_: Promise.resolve("async")
                ),
            },
        )
    )
    result = execute(schema, parse("{ first items last }"), **EXECUTION_OPTIONS)
    assert result.data == {"first": "sync", "items": values, "last": "async"}
    assert list(result.data) == ["first", "items", "last"]
    assert result.errors is None


@pytest.mark.parametrize("promised_type", [False, True])
@pytest.mark.parametrize("matches", [False, True])
def test_abstract_and_object_completion_preserve_predicate_errors(
    promised_type, matches
):
    child = GraphQLObjectType(
        "Child",
        {"name": GraphQLField(GraphQLString)},
        is_type_of=lambda *_: Promise.resolve(matches),
    )
    union = GraphQLUnionType(
        "Result",
        [child],
        resolve_type=lambda *_: Promise.resolve("Child") if promised_type else "Child",
    )
    schema = GraphQLSchema(
        GraphQLObjectType(
            "Query", {"child": GraphQLField(union, resolve=lambda *_: {"name": "Ada"})}
        )
    )
    result = execute(
        schema, parse("{ child { ... on Child { name } } }"), **EXECUTION_OPTIONS
    )
    assert result.data == {"child": {"name": "Ada"} if matches else None}
    if matches:
        assert result.errors is None
    else:
        assert len(result.errors) == 1
        assert result.errors[0].path == ["child"]
        assert result.errors[0].message.startswith("Expected value of type 'Child'")
