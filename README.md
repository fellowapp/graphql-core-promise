# graphql-core-promise

[![GitHub Workflow Status (main)](https://img.shields.io/github/actions/workflow/status/fellowapp/graphql-core-promise/test.yml?branch=main&style=flat)][main CI]
[![PyPI](https://img.shields.io/pypi/v/graphql-core-promise?style=flat)][package]
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/graphql-core-promise?style=flat)][package]
[![License](https://img.shields.io/pypi/l/prosemirror.svg?style=flat)](https://github.com/fellowapp/graphql-core-promise/blob/master/LICENSE.md)
[![Fellow Careers](https://img.shields.io/badge/fellow.app-hiring-576cf7.svg?style=flat)](https://fellow.app/careers/)

[main CI]: https://github.com/fellowapp/graphql-core-promise/actions?query=workflow%3ACI+branch%3Amain
[package]: https://pypi.org/project/graphql-core-promise/

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
