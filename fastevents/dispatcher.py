from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from .events import EventContext, RuntimeEventView, StandardEvent
from .subscribers import DependencyScope, Subscriber, SubscriberResult

logger = logging.getLogger(__name__)

ErrorHook = Callable[[StandardEvent, Subscriber, BaseException], Awaitable[None] | None]


@dataclass(slots=True, frozen=True)
class SubscriberSnapshot:
    """Read-only runtime view of one registered subscriber."""

    id: str
    name: str | None
    level: int
    closed: bool


@dataclass(slots=True, frozen=True)
class DispatcherSnapshot:
    """Read-only snapshot of the current dispatcher registry."""

    subscribers: tuple[SubscriberSnapshot, ...]


class Dispatcher:
    """Semantic core that matches subscribers and applies level propagation."""

    def __init__(self, *, runtime_publisher, error_hook: ErrorHook | None = None) -> None:
        self._subscribers: dict[str, Subscriber] = {}
        self._runtime_publisher = runtime_publisher
        self._error_hook = error_hook

    def bind_runtime_publisher(self, publisher) -> None:
        """Bind the runtime publisher used to build handler event contexts."""

        self._runtime_publisher = publisher

    def add_subscriber(self, subscriber: Subscriber) -> Subscriber:
        """Register one subscriber in the dispatcher registry."""

        self._subscribers[subscriber.id] = subscriber
        return subscriber

    def remove_subscriber(self, subscriber_id: str) -> None:
        """Remove one subscriber from the dispatcher registry if present."""

        self._subscribers.pop(subscriber_id, None)

    def snapshot(self) -> DispatcherSnapshot:
        """Export a read-only snapshot of registered subscribers."""

        return DispatcherSnapshot(
            subscribers=tuple(
                SubscriberSnapshot(
                    id=subscriber.id,
                    name=subscriber.name,
                    level=subscriber.level,
                    closed=subscriber.closed,
                )
                for subscriber in self._subscribers.values()
            )
        )

    async def dispatch(self, event: StandardEvent) -> None:
        """Dispatch one event across matching subscribers by ascending level."""

        matched = [
            subscriber
            for subscriber in self._subscribers.values()
            if not subscriber.closed and subscriber.should_handle(event)
        ]
        if not matched:
            return

        groups: dict[int, list[Subscriber]] = {}
        for subscriber in matched:
            groups.setdefault(subscriber.level, []).append(subscriber)

        runtime_event = RuntimeEventView(event, EventContext(self._runtime_publisher, event))
        dependency_scope = DependencyScope(event=runtime_event, cache={}, resolving=set())
        for level in sorted(groups):
            results = await asyncio.gather(
                *(subscriber.handle(runtime_event, dependency_scope) for subscriber in groups[level]),
                return_exceptions=True,
            )
            level_consumed = False
            for subscriber, result in zip(groups[level], results, strict=True):
                if isinstance(result, BaseException):
                    actual = SubscriberResult(consumed=True, exc=result)
                else:
                    actual = result
                if actual.exc is not None:
                    await self._report_error(event, subscriber, actual.exc)
                if level >= 0 and actual.consumed:
                    level_consumed = True
            if level >= 0 and level_consumed:
                return

    async def _report_error(self, event: StandardEvent, subscriber: Subscriber, exc: BaseException) -> None:
        if self._error_hook is None:
            logger.exception("subscriber %s failed for event %s", subscriber.id, event.id, exc_info=exc)
            return
        result = self._error_hook(event, subscriber, exc)
        if asyncio.iscoroutine(result):
            await result
