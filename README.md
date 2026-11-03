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

One release supports graphql-core 3.2 and 3.3, using a separate promise adapter
for each version. Python 3.11 or newer is required; CI tests Python 3.11–3.15.

### graphql-core 3.2

Use `PromiseExecutionContext` as a replacement for the default execution context:

```python
from graphql_core_promise import PromiseExecutionContext
from graphql.execution.execute import execute

execute(schema=..., document=..., execution_context_class=PromiseExecutionContext)
```

### graphql-core 3.3

Use `PromiseExecutor` with upstream's renamed `executor_class` argument:

```python
from graphql import execute
from graphql_core_promise import PromiseExecutor

execute(schema=..., document=..., executor_class=PromiseExecutor)
```

Both names are exported on both versions and refer to the adapter for the
installed graphql-core version. The keyword passed to graphql-core must match
its version: `execution_context_class` on 3.2 and `executor_class` on 3.3.

The 3.3 adapter supports synchronous query and mutation execution with promise
resolvers and DataLoaders. Query fields batch together; mutation fields execute
serially, including completion of each field's nested selections. The existing
3.2 adapter retains its behavior, including batching across mutation fields.

Incremental delivery (`@defer` and `@stream`), subscriptions, cancellation of
pending promises, and mixing asyncio resolvers with promise resolvers are outside
the supported execution contract.

### With Django (graphql-core 3.2)

graphene-django's `GraphqlView` accepts a `execution_context_class` argument in the constructor. Or you can specify it as a class variable when subclassing.

For example:

```python
view = GraphQLView.as_view(execution_context_class=PromiseExecutionContext)


# OR
class MyGraphQLView(GraphQLView):
    execution_context_class = PromiseExecutionContext
```

Note that this project requires graphene-django 3, which is not fully compatible with graphene-django 2.

The examples above use the graphql-core 3.2 integration API. Using this package
with graphql-core 3.3 also requires the surrounding framework to support 3.3
and its `executor_class` API.

### Versioning and compatibility

This package uses its own version numbers; they do not match graphql-core's.
Adding support for an upstream version while retaining existing APIs is a minor
release. Dropping a supported upstream minor line or removing a public API
requires a major release. The planned release for dual 3.2/3.3 support is 3.5.0.

The dependency range is `graphql-core>=3.2,<3.4,!=3.3.0`. Support for 3.3 starts
at 3.3.1, which fixes security and execution regressions in 3.3.0. Upstream minor
versions can introduce breaking changes, so 3.4 will require a separate evaluation.

Installing this package can select graphql-core 3.3. Applications that need to
remain on 3.2 should declare their own `graphql-core~=3.2.0` dependency. This
adapter does not make other GraphQL integrations compatible with upstream API
changes.

CI runs the shared batching tests against both upstream lines and regression
tests for the 3.3 adapter on Python 3.11–3.15. Ruff's `UP` rules enforce syntax
upgrades compatible with the Python 3.11 minimum. The ty type checks run
separately against each graphql-core version, targeting Python 3.11 and
ignoring the adapter that cannot be imported in that environment:

```sh
# In an environment with graphql-core 3.2:
ty check --exclude graphql_core_promise/execute/executor.py

# In an environment with graphql-core 3.3:
ty check --exclude graphql_core_promise/execute/promise.py
```

### How it works

The adapters translate graphql-core's asyncio execution methods into promise
chains and resolve the completed operation synchronously.
