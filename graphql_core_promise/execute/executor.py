"""Promise execution adapter for graphql-core 3.3."""

from collections.abc import Awaitable
from functools import partial
from typing import Any, TypeGuard, cast

from graphql import GraphQLError
from graphql.execution.collect_fields import FieldDetailsList, GroupedFieldSet
from graphql.execution.executor import (
    Executor,
    default_type_resolver,
    get_typename,
    invalid_return_type_error,
    to_nodes,
)
from graphql.execution.values import get_argument_values
from graphql.pyutils import Path, Undefined, is_iterable
from graphql.type import (
    GraphQLAbstractType,
    GraphQLList,
    GraphQLObjectType,
    GraphQLOutputType,
    GraphQLResolveInfo,
    GraphQLTypeResolver,
)
from promise import Promise, is_thenable


def is_promise(value: Any) -> TypeGuard[Awaitable[Any]]:
    return is_thenable(value)


def promise_type_resolver(
    value: Any, info: GraphQLResolveInfo, abstract_type: GraphQLAbstractType
) -> Any:
    """Resolve abstract types without introducing asyncio work for predicates."""
    type_name = get_typename(value)
    if isinstance(type_name, str):
        return type_name

    pending = []
    pending_types = []
    for type_ in info.schema.get_possible_types(abstract_type):
        if type_.is_type_of:
            matches = type_.is_type_of(value, info)
            if is_thenable(matches):
                pending.append(matches)
                pending_types.append(type_)
            elif matches:
                return type_.name

    if not pending:
        return None

    def select_type(matches: list[bool]) -> str | None:
        return next(
            (
                type_.name
                for type_, match in zip(pending_types, matches, strict=True)
                if match
            ),
            None,
        )

    return Promise.all(pending).then(select_type)


class PromiseExecutor(Executor):
    """Execute synchronous operations with promise resolvers and DataLoaders.

    The root selection set is resolved inside one promise scheduling boundary.
    Nested completion keeps promises intact so sibling query fields can batch.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.is_awaitable = is_promise
        if self.type_resolver is default_type_resolver:
            self.type_resolver = cast(GraphQLTypeResolver, promise_type_resolver)

    def execute_root_grouped_field_set(
        self,
        root_type: GraphQLObjectType,
        root_value: Any,
        grouped_field_set: GroupedFieldSet,
        serially: bool,
        position_context: Any,
    ) -> dict[str, Any]:
        execute_root = super().execute_root_grouped_field_set
        return (
            Promise
            .resolve(None)
            .then(
                lambda _: execute_root(
                    root_type, root_value, grouped_field_set, serially, position_context
                )
            )
            .get()
        )

    def execute_fields(
        self,
        parent_type: GraphQLObjectType,
        source_value: Any,
        path: Path | None,
        grouped_field_set: GroupedFieldSet,
        position_context: Any,
    ) -> Any:
        results = {}
        pending_names = []
        for response_name, field_details in grouped_field_set.items():
            result = self.execute_field(
                parent_type,
                source_value,
                field_details,
                Path(path, response_name, parent_type.name),
                position_context,
            )
            if result is not Undefined:
                results[response_name] = result
                if is_thenable(result):
                    pending_names.append(response_name)

        if not pending_names:
            return results

        def set_results(values: list[Any]) -> dict[str, Any]:
            results.update(zip(pending_names, values, strict=True))
            return results

        return Promise.all([results[name] for name in pending_names]).then(set_results)

    def execute_fields_serially(
        self,
        parent_type: GraphQLObjectType,
        source_value: Any,
        path: Path | None,
        grouped_field_set: GroupedFieldSet,
        position_context: Any,
    ) -> Any:
        results: Promise = Promise.resolve({})

        def execute_next(
            response_name: str,
            field_details: FieldDetailsList,
            resolved: dict[str, Any],
        ) -> Any:
            if self.abort_signal is not None and self.abort_signal.aborted:
                raise self.abort_error()
            value = self.execute_field(
                parent_type,
                source_value,
                field_details,
                Path(path, response_name, parent_type.name),
                position_context,
            )
            if value is Undefined:
                return resolved

            def set_result(completed: Any) -> dict[str, Any]:
                resolved[response_name] = completed
                return resolved

            return Promise.resolve(value).then(set_result)

        for response_name, field_details in grouped_field_set.items():
            results = results.then(partial(execute_next, response_name, field_details))
        return results

    def execute_field(
        self,
        parent_type: GraphQLObjectType,
        source: Any,
        field_details_list: FieldDetailsList,
        path: Path,
        position_context: Any,
    ) -> Any:
        first_field = field_details_list[0]
        field_def = self.schema.get_field(parent_type, first_field.node.name.value)
        if field_def is None:
            return Undefined

        return_type = field_def.type
        resolve = field_def.resolve or self.field_resolver
        if self.middleware_manager:
            resolve = self.middleware_manager.get_field_resolver(resolve)
        info = self.build_resolve_info(
            field_def, to_nodes(field_details_list), parent_type, path
        )

        def complete(result: Any) -> Any:
            return self.complete_value(
                return_type, field_details_list, info, path, result, position_context
            )

        def handle_error(error: Exception) -> None:
            self.handle_field_error(error, return_type, field_details_list, path)

        try:
            args = get_argument_values(
                field_def,
                first_field.node,
                self.variable_values,
                first_field.fragment_variable_values,
                self.hide_suggestions,
            )
            result = resolve(source, info, **args)
            if is_thenable(result):
                # Catch completion failures as well as resolver rejections.
                return Promise.resolve(result).then(complete).catch(handle_error)
            completed = complete(result)
            if is_thenable(completed):
                return Promise.resolve(completed).catch(handle_error)
            return completed
        except Exception as error:
            handle_error(error)
            return None

    def complete_object_value(
        self,
        return_type: GraphQLObjectType,
        field_details_list: FieldDetailsList,
        info: GraphQLResolveInfo,
        path: Path,
        result: Any,
        position_context: Any,
    ) -> Any:
        def complete(matches: bool) -> Any:
            if not matches:
                raise invalid_return_type_error(return_type, result, field_details_list)
            return self.collect_and_execute_subfields(
                return_type, field_details_list, path, result, position_context
            )

        if return_type.is_type_of:
            matches = return_type.is_type_of(result, info)
            if is_thenable(matches):
                return Promise.resolve(matches).then(complete)
            return complete(cast(bool, matches))
        return complete(True)

    def complete_abstract_value(
        self,
        return_type: GraphQLAbstractType,
        field_details_list: FieldDetailsList,
        info: GraphQLResolveInfo,
        path: Path,
        result: Any,
        position_context: Any,
    ) -> Any:
        resolve_type = return_type.resolve_type or self.type_resolver
        runtime_type = resolve_type(result, info, return_type)

        def complete(type_name: Any) -> Any:
            return self.complete_object_value(
                self.ensure_valid_runtime_type(
                    type_name, return_type, field_details_list, info, result
                ),
                field_details_list,
                info,
                path,
                result,
                position_context,
            )

        if is_thenable(runtime_type):
            return Promise.resolve(runtime_type).then(complete)
        return complete(runtime_type)

    def complete_list_value(
        self,
        return_type: GraphQLList[GraphQLOutputType],
        field_details_list: FieldDetailsList,
        info: GraphQLResolveInfo,
        path: Path,
        result: Any,
        position_context: Any,
    ) -> Any:
        if not is_iterable(result):
            msg = (
                "Expected Iterable, but did not find one for field"
                f" '{info.parent_type.name}.{info.field_name}'."
            )
            raise GraphQLError(msg)

        item_type = return_type.of_type

        def complete_item(item: Any, item_path: Path) -> Any:
            def complete(value: Any) -> Any:
                return self.complete_value(
                    item_type,
                    field_details_list,
                    info,
                    item_path,
                    value,
                    position_context,
                )

            def handle_error(error: Exception) -> None:
                self.handle_field_error(error, item_type, field_details_list, item_path)

            try:
                if is_thenable(item):
                    return Promise.resolve(item).then(complete).catch(handle_error)
                completed = complete(item)
                if is_thenable(completed):
                    return Promise.resolve(completed).catch(handle_error)
                return completed
            except Exception as error:
                handle_error(error)
                return None

        completed = [
            complete_item(item, path.add_key(index, None))
            for index, item in enumerate(result)
        ]
        if any(is_thenable(item) for item in completed):
            return Promise.all(completed)
        return completed
