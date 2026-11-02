"""Promise scheduling and result collection shared by the execution adapters.

These helpers propagate errors. Each adapter owns its completion and rejection
handlers so that graphql-core 3.2 and 3.3 can retain their different behavior.
"""

from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias, TypeGuard, TypeVar

import promise
from promise import Promise

T = TypeVar("T")
PromiseOrValue: TypeAlias = Promise[T] | T


def is_thenable(value: Any) -> bool:
    return promise.is_thenable(value)


def is_promise(value: Any) -> TypeGuard[Awaitable[Any]]:
    return is_thenable(value)


def run_in_promise(callback: Callable[[], PromiseOrValue[T]]) -> T:
    """Run a root operation inside the promise scheduler and wait for its result."""
    return Promise.resolve(None).then(lambda _: callback()).get()


def then_or_value(
    value: Any, on_resolved: Callable[[Any], PromiseOrValue[T]]
) -> PromiseOrValue[T]:
    """Apply a completion callback without adding any rejection handling."""
    if is_thenable(value):
        return Promise.resolve(value).then(on_resolved)
    return on_resolved(value)


def _resolve_pending(
    values: list[Any], on_resolved: Callable[[list[Any]], T]
) -> Promise[T]:
    if len(values) == 1:
        return Promise.resolve(values[0]).then(lambda value: on_resolved([value]))
    return Promise.all(values).then(on_resolved)


def collect_dict_results(
    results: dict[str, Any], pending_names: list[str]
) -> PromiseOrValue[dict[str, Any]]:
    """Resolve pending fields in place, preserving field order and sync results."""
    if not pending_names:
        return results

    def set_results(values: list[Any]) -> dict[str, Any]:
        results.update(zip(pending_names, values, strict=True))
        return results

    return _resolve_pending([results[name] for name in pending_names], set_results)


def collect_list_results(
    results: list[Any], pending_indices: list[int] | None = None
) -> PromiseOrValue[list[Any]]:
    """Resolve pending items in place, preserving their positions in the list."""
    if pending_indices is None:
        pending_indices = [
            index for index, item in enumerate(results) if is_thenable(item)
        ]
    if not pending_indices:
        return results

    def set_results(values: list[Any]) -> list[Any]:
        for index, value in zip(pending_indices, values, strict=True):
            results[index] = value
        return results

    return _resolve_pending([results[index] for index in pending_indices], set_results)
