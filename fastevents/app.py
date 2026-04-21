from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .dispatcher import Dispatcher
from .events import StandardEvent
from .exceptions import BusNotStartedError
from .subscribers import EventStream, HandlerSubscriber, StreamSubscriber
from .subscription import SubscriptionInput, TagInput

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

    async def publish(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        meta: dict[str, object] | None = None,
    ) -> StandardEvent:
        """Publish one event through the active runtime bus."""

        bus = self._require_runtime_bus()
        return await bus.publish(tags=tags, payload=payload, meta=meta)

    def _bind_runtime_bus(self, bus: Bus | None) -> None:
        self._runtime_bus = bus

    def _require_runtime_bus(self) -> Bus:
        if self._runtime_bus is None:
            raise BusNotStartedError("app is not bound to a started bus")
        return self._runtime_bus
