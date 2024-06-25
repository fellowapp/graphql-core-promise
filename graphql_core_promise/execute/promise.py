from collections.abc import AsyncIterable, Callable, Iterable
from functools import partial
from typing import (
    Any,
    TypeAlias,
    TypeVar,
    cast,
)

import promise
from graphql.error import located_error
from graphql.error.graphql_error import GraphQLError
from graphql.execution.execute import (
    ExecutionContext,
    get_field_def,
    invalid_return_type_error,
)
from graphql.execution.middleware import MiddlewareManager
from graphql.execution.values import get_argument_values
from graphql.language.ast import (
    FieldNode,
    FragmentDefinitionNode,
    OperationDefinitionNode,
)
from graphql.pyutils import AwaitableOrValue, is_iterable
from graphql.pyutils.path import Path
from graphql.pyutils.undefined import Undefined
from graphql.type import GraphQLSchema
from graphql.type.definition import (
    GraphQLAbstractType,
    GraphQLFieldResolver,
    GraphQLList,
    GraphQLObjectType,
    GraphQLOutputType,
    GraphQLResolveInfo,
    GraphQLTypeResolver,
)
from promise import Promise

T = TypeVar("T")
PromiseOrValue: TypeAlias = Promise[T] | T


def is_thenable(value: Any) -> bool:
    return promise.is_thenable(value)


class PromiseExecutionContext(ExecutionContext):
    """
    Translate methods on the original graphql.execution.execute.ExecutionContext
    to be promise-aware and promise-based so that promise-based dataloaders and
    resolvers can continue to function
    """

    def __init__(
        self,
        schema: GraphQLSchema,
        fragments: dict[str, FragmentDefinitionNode],
        root_value: Any,
        context_value: Any,
        operation: OperationDefinitionNode,
        variable_values: dict[str, Any],
        field_resolver: GraphQLFieldResolver,
        type_resolver: GraphQLTypeResolver,
        subscribe_field_resolver: GraphQLFieldResolver,
        errors: list[GraphQLError],
        middleware_manager: MiddlewareManager | None,
        is_awaitable: Callable[[Any], bool] | None,
    ) -> None:
        super().__init__(
            schema=schema,
            fragments=fragments,
            root_value=root_value,
            context_value=context_value,
            operation=operation,
            variable_values=variable_values,
            field_resolver=field_resolver,
            type_resolver=type_resolver,
            subscribe_field_resolver=subscribe_field_resolver,
            errors=errors,
            middleware_manager=middleware_manager,
            is_awaitable=None,
        )

    is_awaitable = is_promise = staticmethod(is_thenable)

    def execute_operation(
        self, operation: OperationDefinitionNode, root_value: Any
    ) -> AwaitableOrValue[Any] | None:
        super_exec = super().execute_operation
        result = Promise.resolve(None).then(lambda _: super_exec(operation, root_value))
        return result.get()

    def execute_fields_serially(  # type: ignore[override]
        self,
        parent_type: GraphQLObjectType,
        source_value: Any,
        path: Path | None,
        fields: dict[str, list[FieldNode]],
    ) -> PromiseOrValue[dict[str, Any]]:
        results: PromiseOrValue[dict[str, Any]] = {}
        is_promise = self.is_promise
        for response_name, field_nodes in fields.items():
            field_path = Path(path, response_name, parent_type.name)
            result = self.execute_field(
                parent_type, source_value, field_nodes, field_path
            )
            if result is Undefined:
                continue
            if is_promise(results):
                # noinspection PyShadowingNames
                def await_and_set_result(
                    results: Promise[dict[str, Any]],
                    response_name: str,
                    result: PromiseOrValue[Any],
                ) -> Promise[dict[str, Any]]:
                    def handle_results(resolved_results):
                        if is_promise(result):

                            def on_resolve(v):
                                resolved_results[response_name] = v
                                return resolved_results

                            return result.then(on_resolve)
                        resolved_results[response_name] = result
                        return resolved_results

                    results.then(handle_results)
                    return results

                results = await_and_set_result(
                    cast(Promise, results), response_name, result
                )
            elif is_promise(result):
                # noinspection PyShadowingNames
                def set_result(
                    results: dict[str, Any],
                    response_name: str,
                    result: Promise,
                ) -> Promise[dict[str, Any]]:
                    def on_resolve(v):
                        results[response_name] = v
                        return results

                    return result.then(on_resolve)

                results = set_result(
                    cast(dict[str, Any], results), response_name, result
                )
            else:
                cast(dict[str, Any], results)[response_name] = result
        return results

    def execute_field(
        self,
        parent_type: GraphQLObjectType,
        source: Any,
        field_nodes: list[FieldNode],
        path: Path,
    ) -> PromiseOrValue[Any]:
        field_def = get_field_def(self.schema, parent_type, field_nodes[0])
        if not field_def:
            return Undefined

        return_type = field_def.type
        resolve_fn = field_def.resolve or self.field_resolver

        if self.middleware_manager:
            resolve_fn = self.middleware_manager.get_field_resolver(resolve_fn)

        info = self.build_resolve_info(field_def, field_nodes, parent_type, path)

        # Get the resolve function, regardless of if its result is normal or abrupt
        # (error).
        try:
            # Build a dictionary of arguments from the field.arguments AST, using the
            # variables scope to fulfill any variable references.
            args = get_argument_values(field_def, field_nodes[0], self.variable_values)

            # Note that contrary to the JavaScript implementation, we pass the context
            # value as part of the resolve info.
            result = resolve_fn(source, info, **args)

            if self.is_promise(result):
                assert isinstance(result, Promise)

                # noinspection PyShadowingNames
                def await_result() -> Any:
                    def handle_error(raw_error):
                        error = located_error(raw_error, field_nodes, path.as_list())
                        self.handle_field_error(error, return_type)

                    p = result.then(
                        partial(
                            self.complete_value, return_type, field_nodes, info, path
                        ),
                        handle_error,
                    )
                    return p

                return await_result()

            completed = self.complete_value(
                return_type, field_nodes, info, path, result
            )
            if self.is_promise(completed):
                assert isinstance(completed, Promise)

                # noinspection PyShadowingNames
                def await_completed() -> Any:
                    def handle_error(raw_error):
                        error = located_error(raw_error, field_nodes, path.as_list())
                        self.handle_field_error(error, return_type)

                    p = completed.then(lambda v: v, handle_error)
                    return p

                return await_completed()

            return completed
        except Exception as raw_error:
            error = located_error(raw_error, field_nodes, path.as_list())
            self.handle_field_error(error, return_type)
            return None

    def execute_fields(  # type: ignore[override]
        self,
        parent_type: GraphQLObjectType,
        source_value: Any,
        path: Path | None,
        fields: dict[str, list[FieldNode]],
    ) -> PromiseOrValue[dict[str, Any]]:
        results = {}
        is_promise = self.is_promise
        awaitable_fields: list[str] = []
        append_awaitable = awaitable_fields.append
        for response_name, field_nodes in fields.items():
            field_path = Path(path, response_name, parent_type.name)
            result = self.execute_field(
                parent_type, source_value, field_nodes, field_path
            )
            if result is not Undefined:
                results[response_name] = result
                if is_promise(result):
                    append_awaitable(response_name)

        if not awaitable_fields:
            return results

        def get_results() -> PromiseOrValue[dict[str, Any]]:
            r = [results[field] for field in awaitable_fields]
            if len(r) > 1:

                def on_all_resolve(resolved_results: list[Any]):
                    for field, result in zip(
                        awaitable_fields, resolved_results, strict=False
                    ):
                        results[field] = result
                    return results

                p = Promise.all(r).then(on_all_resolve)
            else:

                def on_single_resolve(resolved):
                    results[awaitable_fields[0]] = resolved
                    return results

                return r[0].then(on_single_resolve)
            return p  # type: ignore

        return get_results()

    def complete_object_value(  # type: ignore[override]
        self,
        return_type: GraphQLObjectType,
        field_nodes: list[FieldNode],
        info: GraphQLResolveInfo,
        path: Path,
        result: Any,
    ) -> PromiseOrValue[dict[str, Any]]:
        # Collect sub-fields to execute to complete this value.
        sub_field_nodes = self.collect_subfields(return_type, field_nodes)

        # If there is an `is_type_of()` predicate function, call it with the current
        # result. If `is_type_of()` returns False, then raise an error rather than
        #  continuing execution.
        if return_type.is_type_of:
            is_type_of = return_type.is_type_of(result, info)

            if self.is_promise(is_type_of):
                assert isinstance(is_type_of, Promise)

                def execute_subfields_async() -> PromiseOrValue[dict[str, Any]]:
                    def on_is_type_of_resolve(v):
                        if not v:
                            raise (
                                invalid_return_type_error(
                                    return_type, result, field_nodes
                                )
                            )
                        else:
                            execute_result = self.execute_fields(
                                return_type, result, path, sub_field_nodes
                            )
                            return execute_result

                    return is_type_of.then(on_is_type_of_resolve)

                return execute_subfields_async()

            if not is_type_of:
                raise invalid_return_type_error(return_type, result, field_nodes)

        return self.execute_fields(return_type, result, path, sub_field_nodes)

    def complete_abstract_value(
        self,
        return_type: GraphQLAbstractType,
        field_nodes: list[FieldNode],
        info: GraphQLResolveInfo,
        path: Path,
        result: Any,
    ) -> PromiseOrValue[Any]:
        resolve_type_fn = return_type.resolve_type or self.type_resolver
        runtime_type = resolve_type_fn(result, info, return_type)  # type: ignore

        if self.is_promise(runtime_type):
            assert isinstance(runtime_type, Promise)

            def await_complete_object_value() -> Any:
                def on_runtime_type_resolve(resolved_runtime_type):
                    return self.complete_object_value(
                        self.ensure_valid_runtime_type(
                            resolved_runtime_type,
                            return_type,
                            field_nodes,
                            info,
                            result,
                        ),
                        field_nodes,
                        info,
                        path,
                        result,
                    )

                value = runtime_type.then(on_runtime_type_resolve)
                return value

            return await_complete_object_value()
        runtime_type = cast(str | None, runtime_type)

        return self.complete_object_value(
            self.ensure_valid_runtime_type(
                runtime_type, return_type, field_nodes, info, result
            ),
            field_nodes,
            info,
            path,
            result,
        )

    def complete_list_value(  # type: ignore[override]
        self,
        return_type: GraphQLList[GraphQLOutputType],
        field_nodes: list[FieldNode],
        info: GraphQLResolveInfo,
        path: Path,
        result: Iterable[Any] | Iterable[Promise[Any]],
    ) -> PromiseOrValue[list[Any]]:
        """Complete a list value.

        Complete a list value by completing each item in the list with the inner type.
        """
        if not is_iterable(result):
            # experimental: allow async iterables
            if isinstance(result, AsyncIterable):
                # This should never happen in a promise context
                msg = "what's going on?"
                raise Exception(msg)

            msg = (
                "Expected Iterable, but did not find one for field"
                f" '{info.parent_type.name}.{info.field_name}'."
            )
            raise GraphQLError(msg)
        result = cast(Iterable[Any], result)

        # This is specified as a simple map, however we're optimizing the path where
        # the list contains no coroutine objects by avoiding creating another coroutine
        # object.
        item_type = return_type.of_type
        is_promise = self.is_promise
        awaitable_indices: list[int] = []
        append_awaitable = awaitable_indices.append
        completed_results: list[Any] = []
        append_result = completed_results.append
        for index, item in enumerate(result):
            # No need to modify the info object containing the path, since from here on
            # it is not ever accessed by resolver functions.
            item_path = path.add_key(index, None)
            completed_item: PromiseOrValue[Any]
            if is_promise(item):
                # noinspection PyShadowingNames
                def await_completed(item: Promise[Any], item_path: Path) -> Any:
                    try:

                        def on_item_resolve(item_value):
                            completed = self.complete_value(
                                item_type, field_nodes, info, item_path, item_value
                            )
                            return completed

                        return item.then(on_item_resolve)
                    except Exception as raw_error:
                        error = located_error(
                            raw_error, field_nodes, item_path.as_list()
                        )
                        self.handle_field_error(error, item_type)
                        return None

                completed_item = await_completed(item, item_path)
            else:
                try:
                    completed_item = self.complete_value(
                        item_type, field_nodes, info, item_path, item
                    )
                    if is_promise(completed_item):
                        assert isinstance(completed_item, Promise)

                        # noinspection PyShadowingNames
                        def await_completed(item: Promise[Any], item_path: Path) -> Any:
                            def on_error(raw_error):
                                error = located_error(
                                    raw_error, field_nodes, item_path.as_list()
                                )
                                self.handle_field_error(error, item_type)

                            return item.catch(on_error)

                        completed_item = await_completed(completed_item, item_path)
                except Exception as raw_error:
                    error = located_error(raw_error, field_nodes, item_path.as_list())
                    self.handle_field_error(error, item_type)
                    completed_item = None

            if is_promise(completed_item):
                append_awaitable(index)
            append_result(completed_item)

        if not awaitable_indices:
            return completed_results

        # noinspection PyShadowingNames
        def get_completed_results() -> PromiseOrValue[list[Any]]:
            if len(awaitable_indices) == 1:

                def on_one_resolved(result):
                    completed_results[index] = result
                    return completed_results

                # If there is only one index, avoid the overhead of parallelization.
                index = awaitable_indices[0]
                return completed_results[index].then(on_one_resolved)

            def on_all_resolve(results):
                for index, result in zip(awaitable_indices, results, strict=False):
                    completed_results[index] = result
                return completed_results

            return Promise.all([
                completed_results[index] for index in awaitable_indices
            ]).then(on_all_resolve)

        res = get_completed_results()
        return res
