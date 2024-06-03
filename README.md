# Graphql core promise

Add support for promise-based dataloaders and resolvers to graphql-core v3+. This aims to make migrating to graphene 3 and graphql-core 3 easier for existing projects.

## Usage

This package provides an `ExecuteContext` that can be used as a drop-in replacement for the default one.

```python
from graphql_core_promise import PromiseExecutionContext
from graphql.execution.execute import execute

execute(schema=..., document=..., execution_context_class=PromiseExecutionContext)
```

### With Django

graphene-django's `GraphqlView` accepts a `execution_context_class` argument in the constructor. Or you can specify it as a class variable when subclassing.

For example:

```python
view = GraphQLView.as_view(execution_context_class=PromiseExecutionContext)
# OR
class MyGraphQLView(GraphQLView):
	execution_context_class = PromiseExecutionContext
```

Note that this project requires graphene-django 3, which is not fully compatible with graphene-django 2.

### How it works

This packages is done by translating the asyncio code in the default `ExecuteContext` into promise based code.
