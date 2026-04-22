from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import update_wrapper
from typing import Any, Generic, Protocol, TypeVar, cast, get_type_hints

from pydantic import BaseModel

from .events import EventContext, RuntimeEvent, StandardEvent
from .exceptions import InjectionError, SessionNotConsumed
from .subscription import SubscriptionInput, matches_subscription, normalize_subscription

DependencyValueT = TypeVar("DependencyValueT")


@dataclass(slots=True)
class SubscriberResult:
    """Normalized subscriber outcome used by dispatcher propagation logic."""

    consumed: bool
    exc: BaseException | None = None


class Subscriber(Protocol):
    id: str
    name: str | None
    level: int
    closed: bool

    def should_handle(self, event: StandardEvent) -> bool: ...

    async def handle(self, event: RuntimeEvent, dependency_scope: "DependencyScope | None" = None) -> SubscriberResult: ...

    async def close(self) -> None: ...


@dataclass(slots=True, frozen=True)
class DependencyCall(Generic[DependencyValueT]):
    dependency: "Dependency[DependencyValueT]"


@dataclass(slots=True)
class DependencyScope:
    event: RuntimeEvent
    cache: dict[Dependency[Any], Any]
    resolving: set[Dependency[Any]]
    debug: Callable[[str], None] | None = None


class Dependency(Generic[DependencyValueT]):
    def __init__(self, callback: Callable[..., DependencyValueT]) -> None:
        self.callback = callback
        update_wrapper(self, callback)
        self.name = getattr(callback, "__name__", callback.__class__.__name__)

    def __call__(self) -> DependencyValueT:
        return cast(DependencyValueT, DependencyCall(self))


def dependency(callback: Callable[..., DependencyValueT]) -> Dependency[DependencyValueT]:
    """Declare a lightweight dependency that can be requested from handlers."""

    return Dependency(callback)


def _is_basemodel_type(annotation: Any) -> bool:
    if not inspect.isclass(annotation):
        return False
    return issubclass(annotation, BaseModel)


def _is_quick_payload_annotation(annotation: Any) -> bool:
    return annotation in {dict, list, tuple, str, int, float, bool, bytes}


class HandlerSubscriber:
    """Handler-backed subscriber with lightweight dependency resolution."""

    def __init__(
        self,
        *,
        id: str,
        callback: Callable[..., Awaitable[Any]],
        subscription: SubscriptionInput,
        level: int = 0,
        name: str | None = None,
    ) -> None:
        if not inspect.iscoroutinefunction(callback):
            raise TypeError("handler callback must be async")

        self.id = id
        self.name = name
        self.level = level
        self.closed = False
        self._callback = callback
        self._subscription = normalize_subscription(subscription)
        self._validate_signature(callback)

    def should_handle(self, event: StandardEvent) -> bool:
        return not self.closed and matches_subscription(self._subscription, event.tags)

    async def handle(self, event: RuntimeEvent, dependency_scope: DependencyScope | None = None) -> SubscriberResult:
        try:
            scope = dependency_scope or DependencyScope(event=event, cache={}, resolving=set())
            kwargs = await _DependencyResolver(scope).build_kwargs(self._callback)
        except Exception as exc:
            if scope.debug is not None:
                scope.debug(f"absorbed injection error subscriber={self.name or self.id} exc={exc!r}")
            return SubscriberResult(consumed=False, exc=None)

        try:
            await self._callback(**kwargs)
        except SessionNotConsumed:
            if self.level >= 0:
                return SubscriberResult(consumed=False, exc=None)
            return SubscriberResult(consumed=True, exc=SessionNotConsumed())
        except Exception as exc:
            return SubscriberResult(consumed=True, exc=exc)
        return SubscriberResult(consumed=True, exc=None)

    async def close(self) -> None:
        self.closed = True

    def _validate_signature(self, callback: Callable[..., Any]) -> None:
        signature = inspect.signature(callback)
        type_hints = get_type_hints(callback)
        for parameter in signature.parameters.values():
            if parameter.kind not in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                raise InjectionError("varargs are not supported")

            if isinstance(parameter.default, DependencyCall):
                continue

            annotation = type_hints.get(parameter.name, parameter.annotation)
            if annotation is inspect.Signature.empty:
                if parameter.default is inspect.Signature.empty:
                    raise InjectionError("required parameters must be injectable")
                continue

            if _is_runtime_event_annotation(annotation) or _is_basemodel_type(annotation) or _is_quick_payload_annotation(annotation) or _get_annotation_provider(annotation) is not None:
                continue

            if parameter.default is inspect.Signature.empty:
                raise InjectionError(f"unsupported required parameter: {parameter.name}")


def _is_runtime_event_annotation(annotation: Any) -> bool:
    return annotation is RuntimeEvent or annotation is StandardEvent or getattr(annotation, "__name__", None) == "RuntimeEvent"


def _get_annotation_provider(annotation: Any) -> Dependency[Any] | None:
    if not inspect.isclass(annotation):
        return None
    raw_provider = None
    for cls in annotation.__mro__:
        candidate = cls.__dict__.get("_provider")
        if candidate is not None:
            raw_provider = candidate
            break
    if raw_provider is None:
        return None
    if isinstance(raw_provider, classmethod):
        provider_factory = raw_provider.__get__(annotation, annotation)
    elif isinstance(raw_provider, staticmethod):
        provider_factory = raw_provider.__get__(None, annotation)
    else:
        raise InjectionError(f"{annotation.__name__}._provider must be a staticmethod or classmethod")

    if issubclass(annotation, BaseModel) and raw_provider is not annotation.__dict__.get("_provider"):
        return None
    signature = inspect.signature(provider_factory)
    positional_parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if positional_parameters:
        raise InjectionError(f"{annotation.__name__}._provider must accept no positional arguments")
    provider = provider_factory()
    if not isinstance(provider, Dependency):
        raise InjectionError(f"{annotation.__name__}._provider must return a dependency")
    return provider


class _DependencyResolver:
    def __init__(self, scope: DependencyScope) -> None:
        self._scope = scope

    async def build_kwargs(self, callback: Callable[..., Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        signature = inspect.signature(callback)
        type_hints = get_type_hints(callback)
        event_param_used = False
        payload_param_used = False
        ctx_param_used = False

        for parameter in signature.parameters.values():
            if parameter.kind not in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                raise InjectionError("varargs are not supported")

            if isinstance(parameter.default, DependencyCall):
                kwargs[parameter.name] = await self.resolve_dependency(parameter.default.dependency)
                continue

            annotation = type_hints.get(parameter.name, parameter.annotation)
            if annotation is inspect.Signature.empty:
                if parameter.default is inspect.Signature.empty:
                    raise InjectionError("required parameters must be injectable")
                continue

            if _is_runtime_event_annotation(annotation):
                if event_param_used:
                    raise InjectionError("only one event parameter is allowed")
                kwargs[parameter.name] = self._scope.event
                event_param_used = True
                continue

            annotation_provider = _get_annotation_provider(annotation)
            if annotation_provider is not None:
                if annotation is EventContext:
                    if ctx_param_used:
                        raise InjectionError("only one context parameter is allowed")
                    ctx_param_used = True
                kwargs[parameter.name] = await self.resolve_dependency(annotation_provider)
                continue

            if _is_basemodel_type(annotation) or _is_quick_payload_annotation(annotation):
                if payload_param_used:
                    raise InjectionError("only one payload parameter is allowed")
                kwargs[parameter.name] = self._resolve_payload(annotation)
                payload_param_used = True
                continue

            if parameter.default is inspect.Signature.empty:
                raise InjectionError(f"unsupported required parameter: {parameter.name}")

        return kwargs

    async def resolve_dependency(self, dependency: Dependency[Any]) -> Any:
        cached = self._scope.cache.get(dependency, _MISSING)
        if cached is not _MISSING:
            return cached
        if dependency in self._scope.resolving:
            raise InjectionError(f"dependency cycle detected: {dependency.name}")

        self._scope.resolving.add(dependency)
        try:
            kwargs = await self.build_kwargs(dependency.callback)
            result = dependency.callback(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            self._scope.cache[dependency] = result
            return result
        finally:
            self._scope.resolving.remove(dependency)

    def _resolve_payload(self, annotation: Any) -> Any:
        payload = self._scope.event.payload

        if annotation is dict:
            if not isinstance(payload, dict):
                raise InjectionError("payload is not a dict")
            return payload

        if annotation is list:
            if not isinstance(payload, list):
                raise InjectionError("payload is not a list")
            return payload

        if annotation is tuple:
            if not isinstance(payload, tuple):
                raise InjectionError("payload is not a tuple")
            return payload

        if annotation in {str, int, float, bool, bytes}:
            if not isinstance(payload, annotation):
                raise InjectionError(f"payload is not a {annotation.__name__}")
            return payload

        if _is_basemodel_type(annotation):
            return annotation.model_validate(payload)

        raise InjectionError("unsupported payload annotation")


_MISSING = object()


class EventStream(AsyncIterator[RuntimeEvent]):
    """Async iterator returned by temporary stream-style subscriptions."""

    def __init__(self, queue: asyncio.Queue[RuntimeEvent | None], on_close: Callable[[], Awaitable[None]]) -> None:
        self._queue = queue
        self._on_close = on_close
        self._closed = False

    def __aiter__(self) -> EventStream:
        return self

    async def __anext__(self) -> RuntimeEvent:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def get(self) -> RuntimeEvent:
        """Receive the next event from the stream."""

        return await self.__anext__()

    async def recv(self) -> RuntimeEvent:
        """Alias for ``get()`` with message-stream style naming."""

        return await self.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._on_close()


class StreamSubscriber:
    """Queue-backed subscriber used by ``listen()`` and request/reply helpers."""

    def __init__(
        self,
        *,
        id: str,
        subscription: SubscriptionInput,
        level: int = 0,
        name: str | None = None,
        maxsize: int = 0,
        extra_predicate: Callable[[StandardEvent], bool] | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.level = level
        self.closed = False
        self._subscription = normalize_subscription(subscription)
        self._queue: asyncio.Queue[RuntimeEvent | None] = asyncio.Queue(maxsize=maxsize)
        self._extra_predicate = extra_predicate

    def should_handle(self, event: StandardEvent) -> bool:
        if self.closed or not matches_subscription(self._subscription, event.tags):
            return False
        if self._extra_predicate is None:
            return True
        return self._extra_predicate(event)

    async def handle(self, event: RuntimeEvent, dependency_scope: DependencyScope | None = None) -> SubscriberResult:
        _ = dependency_scope
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull as exc:
            return SubscriberResult(consumed=True, exc=exc)
        return SubscriberResult(consumed=self.level >= 0, exc=None)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            await self._queue.put(None)

    def stream(self, on_close: Callable[[], Awaitable[None]]) -> EventStream:
        return EventStream(self._queue, on_close)
