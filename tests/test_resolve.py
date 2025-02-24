from graphene import List, ObjectType, Schema, String
from graphene.test import Client
from graphql_core_promise import PromiseExecutionContext
from promise import Promise
from promise.dataloader import DataLoader


class SomeLoader(DataLoader):
    def batch_load_fn(self, keys):
        return Promise.resolve([f"{key=} resolved!" for key in keys])


some_loader = SomeLoader()


class Query(ObjectType):
    some_field = List(String)

    def resolve_some_field(root, _):
        return ["Some string", some_loader.load(10), "Another string"]

def test_resolve_list_results():
    schema = Schema(query=Query)


    client = Client(schema=schema, execution_context_class=PromiseExecutionContext)

    foo = client.execute("query myQuery { someField }")
    assert foo == {"data": {"someField": ["Some string", "key=10 resolved!", "Another string"]}}
