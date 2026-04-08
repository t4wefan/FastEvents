from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, get_origin, get_type_hints

from .events import RuntimeEvent, StandardEvent
from .exceptions import InjectionError, SessionNotConsumed
from .subscription import SubscriptionInput, matches_subscription, normalize_subscription

try:
    from pydantic import BaseModel, ValidationError
except Exception:  # pragma: no cover - optional at runtime
    BaseModel = None  # type: ignore[assignment]
    ValidationError = None  # type: ignore[assignment]


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

    async def handle(self, event: RuntimeEvent) -> SubscriberResult: ...

    async def close(self) -> None: ...


def _is_basemodel_type(annotation: Any) -> bool:
    if BaseModel is None or not inspect.isclass(annotation):
        return False
    return issubclass(annotation, BaseModel)


def _validate_basemodel(annotation: Any, payload: Any) -> Any:
    return annotation.model_validate(payload)


class HandlerSubscriber:
    """Handler-backed subscriber with minimal event and payload injection."""

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
        self._event_param = None
        self._payload_param = None
        self._parse_signature()

    def should_handle(self, event: StandardEvent) -> bool:
        return not self.closed and matches_subscription(self._subscription, event.tags)

    async def handle(self, event: RuntimeEvent) -> SubscriberResult:
        try:
            kwargs = self._inject(event)
        except _RecoverableInjectionFailure:
            return SubscriberResult(consumed=False, exc=None)
        except Exception as exc:
            return SubscriberResult(consumed=True, exc=exc)

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

    def _parse_signature(self) -> None:
        signature = inspect.signature(self._callback)
        type_hints = get_type_hints(self._callback)
        for parameter in signature.parameters.values():
            if parameter.kind not in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                raise InjectionError("varargs are not supported")

            annotation = type_hints.get(parameter.name, parameter.annotation)
            if annotation is inspect.Signature.empty:
                if parameter.default is inspect.Signature.empty:
                    raise InjectionError("required parameters must be injectable")
                continue

            if annotation is RuntimeEvent or annotation is StandardEvent or getattr(annotation, "__name__", None) == "Event":
                if self._event_param is not None:
                    raise InjectionError("only one event parameter is allowed")
                self._event_param = parameter.name
                continue

            origin = get_origin(annotation)
            if annotation is dict or origin is dict or _is_basemodel_type(annotation):
                if self._payload_param is not None:
                    raise InjectionError("only one payload parameter is allowed")
                self._payload_param = (parameter.name, annotation)
                continue

            if parameter.default is inspect.Signature.empty:
                raise InjectionError(f"unsupported required parameter: {parameter.name}")

    def _inject(self, event: RuntimeEvent) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self._event_param is not None:
            kwargs[self._event_param] = event
        if self._payload_param is None:
            return kwargs

        payload_name, annotation = self._payload_param
        payload = event.payload
        if annotation is dict or get_origin(annotation) is dict:
            if not isinstance(payload, dict):
                raise _RecoverableInjectionFailure()
            kwargs[payload_name] = payload
            return kwargs

        if _is_basemodel_type(annotation):
            try:
                kwargs[payload_name] = _validate_basemodel(annotation, payload)
            except Exception as exc:  # pragma: no branch
                if ValidationError is not None and isinstance(exc, ValidationError):
                    raise _RecoverableInjectionFailure() from exc
                raise
            return kwargs

        raise InjectionError("unsupported payload annotation")


class _RecoverableInjectionFailure(Exception):
    pass


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

    async def handle(self, event: RuntimeEvent) -> SubscriberResult:
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
