"""Select the execution adapter for the installed graphql-core minor version."""

from graphql import version_info

if version_info >= (3, 3):
    from .executor import PromiseExecutor

    PromiseExecutionContext = PromiseExecutor
else:
    from .promise import PromiseExecutionContext

    PromiseExecutor = PromiseExecutionContext

__all__ = ["PromiseExecutionContext", "PromiseExecutor"]
