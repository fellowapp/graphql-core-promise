"""Contracts that must survive sharing promise helpers across both adapters."""

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
    "failure", ["completion", "list_reject", "list_completion", "non_null"]
)
def test_error_handling_stays_version_specific(failure):
    def resolve(*_):
        if failure == "list_reject":
            return ["ok", Promise.reject(ValueError("failed"))]
        if failure == "list_completion":
            return ["ok", Promise.resolve({})]
        if failure == "non_null":
            return Promise.reject(ValueError("failed"))
        return Promise.resolve({})

    field_type = GraphQLString
    if failure.startswith("list_"):
        field_type = GraphQLList(GraphQLString)
    elif failure == "non_null":
        field_type = GraphQLNonNull(GraphQLString)
    schema = GraphQLSchema(
        GraphQLObjectType("Query", {"value": GraphQLField(field_type, resolve=resolve)})
    )
    result = execute(schema, parse("{ value }"), **EXECUTION_OPTIONS)

    if failure == "non_null" or (failure == "completion" and not IS_33):
        assert result.data is None
    elif failure.startswith("list_") and IS_33:
        assert result.data == {"value": ["ok", None]}
    else:
        assert result.data == {"value": None}

    assert len(result.errors) == 1
    error = result.errors[0]
    if failure == "completion" and not IS_33:
        assert error.path is None
        assert error.locations is None
    else:
        assert error.path == (
            ["value", 1] if IS_33 and failure.startswith("list_") else ["value"]
        )
        assert [(location.line, location.column) for location in error.locations] == [
            (1, 3)
        ]
    if failure in {"list_reject", "non_null"}:
        assert error.message == "failed"
    else:
        assert error.message == "String cannot represent value: {}"


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
