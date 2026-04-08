from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from time import time
from typing import Any, Protocol
from uuid import uuid4

from .exceptions import ReplyNotAvailableError
from .subscription import TagInput, normalize_tags


@dataclass(slots=True, frozen=True)
class StandardEvent:
    """Immutable event payload shared across runtime and transport boundaries."""

    id: str
    timestamp: float
    tags: tuple[str, ...]
    meta: Mapping[str, Any] = field(default_factory=dict)
    payload: Any = None


def new_event(
    *,
    tags: TagInput,
    payload: Any = None,
    meta: dict[str, Any] | None = None,
    id: str | None = None,
    timestamp: float | None = None,
) -> StandardEvent:
    """Create a normalized ``StandardEvent`` with generated defaults."""

    event_meta = dict(meta or {})
    return StandardEvent(
        id=id or uuid4().hex,
        timestamp=time() if timestamp is None else timestamp,
        tags=normalize_tags(tags),
        meta=event_meta,
        payload=payload,
    )


class BusPublisher(Protocol):
    async def publish(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        reply_tags: TagInput | None = None,
        meta: dict[str, Any] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> StandardEvent: ...


class EventContext:
    """Runtime capability surface exposed to subscriber handlers."""

    def __init__(self, bus: BusPublisher, event: StandardEvent) -> None:
        self._bus = bus
        self._event = event

    async def publish(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
        reply_tags: TagInput | None = None,
        meta: dict[str, Any] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> StandardEvent:
        """Publish a new event through the current runtime bus."""

        return await self._bus.publish(
            tags=tags,
            payload=payload,
            reply_tags=reply_tags,
            meta=meta,
            id=id,
            timestamp=timestamp,
        )

    async def reply(
        self,
        payload: Any = None,
        *,
        tags: TagInput | None = None,
        meta: dict[str, Any] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> StandardEvent:
        """Publish a reply event for the current request/reply chain."""

        target_tags = tags or self._event.meta.get("reply_tags")
        if target_tags is None:
            raise ReplyNotAvailableError("reply_tags not available on current event")

        reply_meta: dict[str, Any] = {}
        correlation_id = self._event.meta.get("correlation_id")
        if correlation_id is not None:
            reply_meta["correlation_id"] = correlation_id
        if meta:
            reply_meta.update(meta)

        return await self._bus.publish(
            tags=target_tags,
            payload=payload,
            meta=reply_meta,
            id=id,
            timestamp=timestamp,
        )


class RuntimeEvent(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def timestamp(self) -> float: ...

    @property
    def tags(self) -> tuple[str, ...]: ...

    @property
    def meta(self) -> Mapping[str, Any]: ...

    @property
    def payload(self) -> Any: ...

    @property
    def ctx(self) -> EventContext: ...


@dataclass(slots=True, frozen=True)
class RuntimeEventView:
    """Concrete runtime view that wraps a standard event with ``ctx``."""

    _event: StandardEvent
    ctx: EventContext

    @property
    def id(self) -> str:
        return self._event.id

    @property
    def timestamp(self) -> float:
        return self._event.timestamp

    @property
    def tags(self) -> tuple[str, ...]:
        return self._event.tags

    @property
    def meta(self) -> Mapping[str, Any]:
        return self._event.meta

    @property
    def payload(self) -> Any:
        return self._event.payload
