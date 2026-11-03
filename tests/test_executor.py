"""Regression coverage for the graphql-core 3.3 adapter."""

from unittest.mock import Mock

import pytest
from graphql import (
    GraphQLArgument,
    GraphQLField,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
    GraphQLUnionType,
    build_schema,
    execute,
    graphql_sync,
    parse,
    version_info,
)
from promise import Promise
from promise.dataloader import DataLoader

from graphql_core_promise import PromiseExecutionContext, PromiseExecutor

pytestmark = pytest.mark.skipif(
    version_info < (3, 3), reason="graphql-core 3.3 adapter"
)


def run(schema, query, **kwargs):
    return execute(schema, parse(query), executor_class=PromiseExecutor, **kwargs)


def test_public_alias_and_synchronous_entry_point():
    assert PromiseExecutionContext is PromiseExecutor
    schema = build_schema("type Query { hello: String }")
    result = graphql_sync(
        schema,
        "{ hello __typename }",
        root_value={"hello": Promise.resolve("world")},
        executor_class=PromiseExecutor,
        check_sync=True,
    )
    assert result.data == {"hello": "world", "__typename": "Query"}
    assert result.errors is None


@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.parametrize("non_null", [False, True])
def test_promised_null_and_rejected_fields(reject, non_null):
    field_type = GraphQLNonNull(GraphQLString) if non_null else GraphQLString
    promised_value = (
        Promise.reject(ValueError("failed")) if reject else Promise.resolve(None)
    )
    schema = GraphQLSchema(
        GraphQLObjectType(
            "Query",
            {"value": GraphQLField(field_type, resolve=lambda *_: promised_value)},
        )
    )
    result = run(schema, "{ value }")
    assert result.data == (None if non_null else {"value": None})
    if reject or non_null:
        assert len(result.errors) == 1
        assert result.errors[0].path == ["value"]
        if reject:
            assert result.errors[0].message == "failed"
    else:
        assert result.errors is None


@pytest.mark.parametrize("promised_parent", [False, True])
def test_nested_completion_error_is_located(promised_parent):
    child = GraphQLObjectType(
        "Child",
        {
            "name": GraphQLField(
                GraphQLNonNull(GraphQLString),
                resolve=lambda *_: Promise.reject(ValueError("child failed")),
            )
        },
    )
    schema = GraphQLSchema(
        GraphQLObjectType(
            "Query",
            {
                "child": GraphQLField(
                    child,
                    resolve=lambda *_: Promise.resolve({}) if promised_parent else {},
                ),
                "sibling": GraphQLField(GraphQLString, resolve=lambda *_: "ok"),
            },
        )
    )
    result = run(schema, "{ child { name } sibling }")
    assert result.data == {"child": None, "sibling": "ok"}
    assert len(result.errors) == 1
    assert result.errors[0].message == "child failed"
    assert result.errors[0].path == ["child", "name"]


@pytest.mark.parametrize("non_null", [False, True])
@pytest.mark.parametrize("failure", ["reject", "null", "invalid"])
def test_list_item_error_paths(non_null, failure):
    item_type = GraphQLNonNull(GraphQLString) if non_null else GraphQLString
    values = {
        "reject": Promise.reject(ValueError("item failed")),
        "null": Promise.resolve(None),
        "invalid": Promise.resolve({"not": "a string"}),
    }
    schema = GraphQLSchema(
        GraphQLObjectType(
            "Query",
            {
                "items": GraphQLField(
                    GraphQLList(item_type),
                    resolve=lambda *_: [
                        "first",
                        values[failure],
                        Promise.resolve("last"),
                    ],
                )
            },
        )
    )
    result = run(schema, "{ items }")
    assert result.data == {"items": None if non_null else ["first", None, "last"]}
    if failure == "null" and not non_null:
        assert result.errors is None
    else:
        assert len(result.errors) == 1
        assert result.errors[0].path == ["items", 1]


@pytest.mark.parametrize("matches", [False, True])
def test_promised_is_type_of(matches):
    child = GraphQLObjectType(
        "Child",
        {"name": GraphQLField(GraphQLString)},
        is_type_of=lambda *_: Promise.resolve(matches),
    )
    schema = GraphQLSchema(
        GraphQLObjectType(
            "Query",
            {"child": GraphQLField(child, resolve=lambda *_: {"name": "Ada"})},
        )
    )
    result = run(schema, "{ child { name } }")
    assert result.data == {"child": {"name": "Ada"} if matches else None}
    if matches:
        assert result.errors is None
    else:
        assert len(result.errors) == 1
        assert result.errors[0].path == ["child"]


@pytest.mark.parametrize("custom_resolver", [False, True])
def test_abstract_type_resolution(custom_resolver):
    cat = GraphQLObjectType(
        "Cat",
        {"name": GraphQLField(GraphQLString)},
        is_type_of=lambda *_: Promise.resolve(False),
    )
    dog = GraphQLObjectType(
        "Dog",
        {"name": GraphQLField(GraphQLString)},
        is_type_of=lambda *_: Promise.resolve(True),
    )
    pet = GraphQLUnionType(
        "Pet",
        [cat, dog],
        resolve_type=(lambda *_: Promise.resolve("Dog")) if custom_resolver else None,
    )
    schema = GraphQLSchema(
        GraphQLObjectType(
            "Query", {"pet": GraphQLField(pet, resolve=lambda *_: {"name": "Rex"})}
        )
    )
    result = run(schema, "{ pet { __typename ... on Dog { name } } }")
    assert result.data == {"pet": {"__typename": "Dog", "name": "Rex"}}
    assert result.errors is None


def test_variables_fragments_directives_and_middleware():
    schema = GraphQLSchema(
        GraphQLObjectType(
            "Query",
            {
                "hello": GraphQLField(
                    GraphQLString,
                    args={"name": GraphQLArgument(GraphQLString)},
                    resolve=lambda _source, info, name: Promise.resolve(
                        info.context + name
                    ),
                )
            },
        )
    )
    calls = []

    def middleware(next_, source, info, **args):
        calls.append((info.field_name, args))
        return next_(source, info, **args).then(str.upper)

    result = run(
        schema,
        """
        query Greeting($name: String = "Ada", $skip: Boolean! = true) {
            ...Greeting
            ignored: hello(name: "ignored") @skip(if: $skip)
        }
        fragment Greeting on Query { greeting: hello(name: $name) }
        """,
        context_value="Hello ",
        middleware=[middleware],
    )
    assert result.data == {"greeting": "HELLO ADA"}
    assert result.errors is None
    assert calls == [("hello", {"name": "Ada"})]


def test_fragment_arguments():
    schema = build_schema("type Query { hello(name: String): String }")
    document = parse(
        """
        query { ...Greeting(name: "Ada") }
        fragment Greeting($name: String) on Query { hello(name: $name) }
        """,
        experimental_fragment_arguments=True,
    )
    result = execute(
        schema,
        document,
        root_value={"hello": lambda _info, name: Promise.resolve(name)},
        executor_class=PromiseExecutor,
    )
    assert result.data == {"hello": "Ada"}
    assert result.errors is None


def test_mutation_waits_for_nested_completion():
    events = []

    class Loader(DataLoader):
        def batch_load_fn(self, keys):
            events.append(("complete", keys))
            return Promise.resolve(keys)

    loader = Loader()
    child = GraphQLObjectType(
        "Child",
        {"value": GraphQLField(GraphQLString, resolve=lambda obj, _: loader.load(obj))},
    )

    def mutate(_source, _info, value):
        events.append(("start", value))
        return Promise.resolve(value)

    schema = GraphQLSchema(
        GraphQLObjectType("Query", {"noop": GraphQLField(GraphQLString)}),
        GraphQLObjectType(
            "Mutation",
            {
                "update": GraphQLField(
                    child,
                    args={"value": GraphQLArgument(GraphQLString)},
                    resolve=mutate,
                )
            },
        ),
    )
    result = run(
        schema,
        'mutation { a: update(value: "a") { value } b: update(value: "b") { value } }',
    )
    assert result.data == {"a": {"value": "a"}, "b": {"value": "b"}}
    assert result.errors is None
    assert events == [
        ("start", "a"),
        ("complete", ["a"]),
        ("start", "b"),
        ("complete", ["b"]),
    ]


def test_non_null_mutation_error_stops_later_fields():
    later = Mock(return_value="unexpected")
    schema = GraphQLSchema(
        GraphQLObjectType("Query", {"noop": GraphQLField(GraphQLString)}),
        GraphQLObjectType(
            "Mutation",
            {
                "fail": GraphQLField(
                    GraphQLNonNull(GraphQLString),
                    resolve=lambda *_: Promise.reject(ValueError("stop")),
                ),
                "later": GraphQLField(GraphQLString, resolve=later),
            },
        ),
    )
    result = run(schema, "mutation { fail later }")
    assert result.data is None
    assert len(result.errors) == 1
    assert result.errors[0].path == ["fail"]
    later.assert_not_called()


def test_list_object_completion_batches_and_preserves_selections():
    batches = Mock()

    class Loader(DataLoader):
        def batch_load_fn(self, keys):
            batches(keys)
            return Promise.resolve(keys)

    loader = Loader()
    child = GraphQLObjectType(
        "Child",
        {
            "name": GraphQLField(
                GraphQLString, resolve=lambda obj, _: loader.load(obj["name"])
            )
        },
    )
    schema = GraphQLSchema(
        GraphQLObjectType(
            "Query",
            {
                "children": GraphQLField(
                    GraphQLList(child),
                    resolve=lambda *_: [
                        Promise.resolve({"name": "Ada"}),
                        {"name": "Grace"},
                    ],
                )
            },
        )
    )
    result = run(schema, "{ children { first: name } children { second: name } }")
    assert result.data == {
        "children": [
            {"first": "Ada", "second": "Ada"},
            {"first": "Grace", "second": "Grace"},
        ]
    }
    assert result.errors is None
    batches.assert_called_once()
    assert set(batches.call_args.args[0]) == {"Ada", "Grace"}
