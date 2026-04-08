from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .dispatcher import Dispatcher
from .events import RuntimeEvent, StandardEvent
from .exceptions import BusNotStartedError, RequestTimeoutError
from .subscribers import EventStream, HandlerSubscriber, StreamSubscriber
from .subscription import SubscriptionInput, TagInput, normalize_tags

if TYPE_CHECKING:
    from .bus import Bus


class FastEvents:
    """Declaration facade used to register event subscribers."""

    def __init__(self) -> None:
        self.dispatcher = Dispatcher(runtime_publisher=None)
        self._runtime_bus: Bus | None = None

    def on(
        self,
        subscription: SubscriptionInput,
        *,
        level: int = 0,
        name: str | None = None,
    ) -> Callable[[Callable[..., Awaitable[object]]], Callable[..., Awaitable[object]]]:
        """Register an async handler for a subscription pattern.

        ``level`` controls propagation order and fallback behavior. ``name`` is
        optional and defaults to the callback function name.
        """

        def decorator(callback: Callable[..., Awaitable[object]]) -> Callable[..., Awaitable[object]]:
            subscriber = HandlerSubscriber(
                id=uuid4().hex,
                callback=callback,
                subscription=subscription,
                level=level,
                name=name or callback.__name__,
            )
            self.dispatcher.add_subscriber(subscriber)
            return callback

        return decorator

    @asynccontextmanager
    async def listen(
        self,
        subscription: SubscriptionInput,
        *,
        level: int = 0,
        name: str | None = None,
        maxsize: int = 0,
    ) -> AsyncIterator[EventStream]:
        """Register a temporary stream subscriber on the running app."""

        bus = self._require_runtime_bus()
        await bus._ensure_runtime_ready()  # type: ignore[attr-defined]
        subscriber = StreamSubscriber(
            id=uuid4().hex,
            subscription=subscription,
            level=level,
            name=name,
            maxsize=maxsize,
        )
        self.dispatcher.add_subscriber(subscriber)

        async def cleanup() -> None:
            await subscriber.close()
            self.dispatcher.remove_subscriber(subscriber.id)

        stream = subscriber.stream(cleanup)
        try:
            yield stream
        finally:
            await stream.close()

    async def request(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, object] | None = None,
        timeout: float | None = None,
        level: int = 0,
    ) -> StandardEvent:
        """Publish one request event and await the first matching reply."""

        bus = self._require_runtime_bus()
        await bus._ensure_runtime_ready()  # type: ignore[attr-defined]
        correlation_id = uuid4().hex
        reply_tags = normalize_tags(("reply", correlation_id))
        request_meta = dict(meta or {})
        request_meta["correlation_id"] = correlation_id

        def predicate(event: StandardEvent) -> bool:
            return event.meta.get("correlation_id") == correlation_id

        subscriber = StreamSubscriber(
            id=uuid4().hex,
            subscription=reply_tags,
            level=level,
            name="request-reply-listener",
            extra_predicate=predicate,
        )
        self.dispatcher.add_subscriber(subscriber)

        async def cleanup() -> None:
            await subscriber.close()
            self.dispatcher.remove_subscriber(subscriber.id)

        stream = subscriber.stream(cleanup)
        try:
            await bus.publish(tags=tags, payload=payload, reply_tags=reply_tags, meta=request_meta)
            try:
                reply_event: RuntimeEvent
                if timeout is None:
                    reply_event = await stream.get()
                else:
                    reply_event = await asyncio.wait_for(stream.get(), timeout=timeout)
                return StandardEvent(
                    id=reply_event.id,
                    timestamp=reply_event.timestamp,
                    tags=reply_event.tags,
                    meta=reply_event.meta,
                    payload=reply_event.payload,
                )
            except TimeoutError as exc:
                raise RequestTimeoutError("request timed out") from exc
        finally:
            await stream.close()

    def _bind_runtime_bus(self, bus: Bus | None) -> None:
        self._runtime_bus = bus

    def _require_runtime_bus(self) -> Bus:
        if self._runtime_bus is None:
            raise BusNotStartedError("app is not bound to a started bus")
        return self._runtime_bus
