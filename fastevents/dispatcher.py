from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from .events import EventContext, RuntimeEventView, StandardEvent, format_event_debug
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

    def __init__(self, *, runtime_publisher, error_hook: ErrorHook | None = None, debug: bool = False, debug_printer: Callable[[str], None] | None = None) -> None:
        self._subscribers: dict[str, Subscriber] = {}
        self._runtime_publisher = runtime_publisher
        self._error_hook = error_hook
        self._debug = debug
        self._debug_printer = debug_printer

    def bind_runtime_publisher(self, publisher) -> None:
        """Bind the runtime publisher used to build handler event contexts."""

        self._runtime_publisher = publisher

    def set_debug(self, debug: bool, debug_printer: Callable[[str], None] | None) -> None:
        self._debug = debug
        self._debug_printer = debug_printer

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
        self._emit_debug(
            f"dispatch start {format_event_debug(event)} matched={[self._subscriber_label(subscriber) for subscriber in matched]}"
        )
        if not matched:
            return

        groups: dict[int, list[Subscriber]] = {}
        for subscriber in matched:
            groups.setdefault(subscriber.level, []).append(subscriber)

        runtime_event = RuntimeEventView(event, EventContext(self._runtime_publisher, event))
        dependency_scope = DependencyScope(event=runtime_event, cache={}, resolving=set(), debug=self._emit_debug if self._debug else None)
        for level in sorted(groups):
            self._emit_debug(
                f"dispatch level={level} subscribers={[self._subscriber_label(subscriber) for subscriber in groups[level]]}"
            )
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
                self._emit_debug(
                    f"dispatch result subscriber={self._subscriber_label(subscriber)} level={level} consumed={actual.consumed} exc={actual.exc!r}"
                )
                if level >= 0 and actual.consumed:
                    level_consumed = True
            if level >= 0 and level_consumed:
                self._emit_debug(f"dispatch stopped at level={level} event={event.id}")
                return
        self._emit_debug(f"dispatch completed event={event.id}")

    async def _report_error(self, event: StandardEvent, subscriber: Subscriber, exc: BaseException) -> None:
        if self._error_hook is None:
            logger.exception("subscriber %s failed for event %s", subscriber.id, event.id, exc_info=exc)
            return
        result = self._error_hook(event, subscriber, exc)
        if asyncio.iscoroutine(result):
            await result

    def _emit_debug(self, message: str) -> None:
        if self._debug and self._debug_printer is not None:
            self._debug_printer(message)

    def _subscriber_label(self, subscriber: Subscriber) -> str:
        return subscriber.name or subscriber.id
