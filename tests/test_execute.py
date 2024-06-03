from unittest.mock import MagicMock, call

from graphql.execution.execute import execute
from graphql.language import parse
from graphql.type import (
    GraphQLArgument,
    GraphQLField,
    GraphQLID,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
)
from graphql.type.definition import GraphQLList
from promise import Promise
from promise.dataloader import DataLoader

from graphql_core_promise.execute import PromiseExecutionContext

author_load_stub = MagicMock()
name_loader_stub = MagicMock()
book_loader_stub = MagicMock()


class AuthorNode:
    pass


class BookNode:
    pass


class AuthorLoader(DataLoader):
    def batch_load_fn(self, keys):
        author_load_stub(keys)
        return Promise.resolve([type(key, (AuthorNode,), {"id": key}) for key in keys])


class NameLoader(DataLoader):
    def batch_load_fn(self, keys):
        name_loader_stub(keys)
        return Promise.resolve([key + " name" for key in keys])


class BooksLoader(DataLoader):
    def batch_load_fn(self, keys):
        book_loader_stub(keys)
        return Promise.resolve([type(key, (BookNode,), {"id": key}) for key in keys])


author_loader = AuthorLoader()
name_loader = NameLoader()
books_loader = BooksLoader()

Book = GraphQLObjectType(
    "Book",
    lambda: {
        "id": GraphQLField(GraphQLString, resolve=lambda obj, _info: obj.id),
        "name": GraphQLField(
            GraphQLString, resolve=lambda obj, _info: name_loader.load(obj.id)
        ),
        "author": GraphQLField(
            Author,
            resolve=lambda obj, _info: author_loader.load(obj.id.split(" ")[0]),
        ),
    },
)

Author = GraphQLObjectType(
    "Author",
    lambda: {
        "id": GraphQLField(GraphQLString, resolve=lambda obj, _info: obj.id),
        "name": GraphQLField(
            GraphQLString, resolve=lambda obj, _info: name_loader.load(obj.id)
        ),
        "books": GraphQLField(
            GraphQLList(Book),
            resolve=lambda obj, _info: books_loader.load_many([
                obj.id + f" book_{i}" for i in range(1, 3)
            ]),
        ),
    },
)

TestQuery = GraphQLObjectType(
    "Query",
    {
        "author": GraphQLField(
            Author,
            args={"id": GraphQLArgument(GraphQLID)},
            resolve=lambda _obj, _info, id: author_loader.load(id),
        ),
        "books": GraphQLField(
            GraphQLList(Book),
            args={"ids": GraphQLArgument(GraphQLList(GraphQLID))},
            resolve=lambda _, __, *, ids: [books_loader.load(id) for id in ids],
        ),
    },
)
TestMutation = GraphQLObjectType(
    "Mutation",
    {
        "newAuthor": GraphQLField(
            Author,
            args={"id": GraphQLArgument(GraphQLString)},
            resolve=lambda obj, info, id: author_loader.load(id),
        )
    },
)

TestSchema = GraphQLSchema(TestQuery, TestMutation)


def test_executes_simple_query(benchmark):
    foo_query = parse(
        """
        query Foo {
            a: author(id: "1") {
            id,
            name,
            }
            b: author(id: "2") {
            id,
            name,
            }
            c: author(id: "2") {
            id,
            name,
            }
        }
        """
    )

    def t():
        assert execute(
            schema=TestSchema,
            document=foo_query,
            execution_context_class=PromiseExecutionContext,
        ) == (
            {
                "a": {"id": "1", "name": "1 name"},
                "b": {"id": "2", "name": "2 name"},
                "c": {"id": "2", "name": "2 name"},
            },
            None,
        )

    benchmark(t)

    author_load_stub.assert_called_once_with(["1", "2"])
    name_loader_stub.assert_called_once_with(["1", "2"])


def test_execute_mutation():
    author_load_stub.reset_mock()
    name_loader_stub.reset_mock()

    author_loader.clear_all()
    books_loader.clear_all()
    name_loader.clear_all()

    foo_mutation = parse(
        """
        mutation Foo {
            a: newAuthor(id: "3") {
                id
                name
            }
            b: newAuthor(id: "4") {
                id
                name
            }
        }
    """
    )

    assert execute(
        schema=TestSchema,
        document=foo_mutation,
        execution_context_class=PromiseExecutionContext,
    ) == (
        {"a": {"id": "3", "name": "3 name"}, "b": {"id": "4", "name": "4 name"}},
        None,
    )

    author_load_stub.assert_called_once_with(["3", "4"])
    name_loader_stub.assert_called_once_with(["3", "4"])


def test_execute_list_results(benchmark):
    author_load_stub.reset_mock()
    name_loader_stub.reset_mock()
    book_loader_stub.reset_mock()

    author_loader.clear_all()
    books_loader.clear_all()
    name_loader.clear_all()

    foo_query = parse(
        """
        query Foo {
            a: author(id: "1") {
            id,
            name,
            books {
                id,
                name,
                author {
                    id
                    name
                    books {
                        id,
                        name,
                    }
                }
            }
            }
            b: author(id: "2") {
                id,
                name,
                books {
                    id,
                    name,
                    author {
                        name
                    }
                }
            }
            books(ids: ["1 book_1", "2 book_1"]) {
                id
                name
            }
        }
        """
    )

    def t():
        assert execute(
            schema=TestSchema,
            document=foo_query,
            execution_context_class=PromiseExecutionContext,
        ) == (
            {
                "a": {
                    "id": "1",
                    "name": "1 name",
                    "books": [
                        {
                            "id": "1 book_1",
                            "name": "1 book_1 name",
                            "author": {
                                "id": "1",
                                "name": "1 name",
                                "books": [
                                    {"id": "1 book_1", "name": "1 book_1 name"},
                                    {"id": "1 book_2", "name": "1 book_2 name"},
                                ],
                            },
                        },
                        {
                            "id": "1 book_2",
                            "name": "1 book_2 name",
                            "author": {
                                "id": "1",
                                "name": "1 name",
                                "books": [
                                    {"id": "1 book_1", "name": "1 book_1 name"},
                                    {"id": "1 book_2", "name": "1 book_2 name"},
                                ],
                            },
                        },
                    ],
                },
                "b": {
                    "id": "2",
                    "name": "2 name",
                    "books": [
                        {
                            "id": "2 book_1",
                            "name": "2 book_1 name",
                            "author": {"name": "2 name"},
                        },
                        {
                            "id": "2 book_2",
                            "name": "2 book_2 name",
                            "author": {"name": "2 name"},
                        },
                    ],
                },
                "books": [
                    {"id": "1 book_1", "name": "1 book_1 name"},
                    {"id": "2 book_1", "name": "2 book_1 name"},
                ],
            },
            None,
        )

    benchmark(t)

    author_load_stub.assert_called_once_with(["1", "2"])
    name_loader_stub.assert_has_calls(
        [call(["1", "2", "1 book_1", "2 book_1"]), call(["1 book_2", "2 book_2"])],
        any_order=True,
    )
    book_loader_stub.assert_has_calls(
        [call(["1 book_1", "2 book_1"]), call(["1 book_2", "2 book_2"])],
        any_order=True,
    )
