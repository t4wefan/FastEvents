from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import time
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel

from .subscription import TagInput, normalize_tags


@dataclass(slots=True, frozen=True)
class StandardEvent:
    """Immutable event payload shared across runtime and transport boundaries."""

    id: str
    timestamp: float
    tags: tuple[str, ...]
    meta: Mapping[str, Any] = field(default_factory=dict)
    payload: Any = None


ScalarEventValue = None | bool | int | float | str


def _is_bus_scalar(value: Any) -> bool:
    return value is None or isinstance(value, bool | int | float | str)


def _normalize_json_compatible(value: Any) -> Any:
    if _is_bus_scalar(value):
        return value
    if isinstance(value, BaseModel):
        return _normalize_json_compatible(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize_json_compatible(asdict(value))
    if isinstance(value, tuple):
        return {"tuple": [_normalize_json_compatible(item) for item in value]}
    if isinstance(value, list):
        return [_normalize_json_compatible(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("event dict keys must be strings")
            normalized[key] = _normalize_json_compatible(item)
        return normalized
    raise TypeError(f"value of type {type(value).__name__} is not event-serializable")


def encode_app_value(value: Any) -> ScalarEventValue:
    """Normalize app-facing values into bus-facing scalar payload/meta values."""

    if _is_bus_scalar(value):
        return value
    normalized = _normalize_json_compatible(value)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def decode_event_value(value: Any) -> Any:
    """Decode one bus-facing scalar into an app-facing structured value when possible."""

    if isinstance(value, list):
        return [decode_event_value(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"tuple"} and isinstance(value["tuple"], list):
            return tuple(decode_event_value(item) for item in value["tuple"])
        return {key: decode_event_value(item) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return decode_event_value(decoded)


def decode_event_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    return {key: decode_event_value(value) for key, value in meta.items()}


def encode_event_meta(meta: Mapping[str, Any] | None) -> dict[str, ScalarEventValue]:
    encoded: dict[str, ScalarEventValue] = {}
    for key, value in dict(meta or {}).items():
        if not isinstance(key, str):
            raise TypeError("event meta keys must be strings")
        encoded[key] = encode_app_value(value)
    return encoded


def new_event(
    *,
    tags: TagInput,
    payload: Any = None,
    meta: dict[str, Any] | None = None,
    id: str | None = None,
    timestamp: float | None = None,
) -> StandardEvent:
    """Create a normalized ``StandardEvent`` with generated defaults."""

    event_meta = encode_event_meta(meta)
    return StandardEvent(
        id=id or uuid4().hex,
        timestamp=time() if timestamp is None else timestamp,
        tags=normalize_tags(tags),
        meta=event_meta,
        payload=encode_app_value(payload),
    )


class BusPublisher(Protocol):
    async def publish(
        self,
        *,
        tags: TagInput,
        payload: Any = None,
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
        meta: dict[str, Any] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> StandardEvent:
        """Publish a new event through the current runtime bus."""

        return await self._bus.publish(
            tags=tags,
            payload=payload,
            meta=meta,
            id=id,
            timestamp=timestamp,
        )

    @staticmethod
    def _provider():
        from .subscribers import dependency

        @dependency
        def provider(event: RuntimeEvent) -> EventContext:
            return event.ctx

        return provider


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
        return decode_event_meta(self._event.meta)

    @property
    def payload(self) -> Any:
        return decode_event_value(self._event.payload)


def format_event_debug(event: StandardEvent | RuntimeEvent) -> str:
    return (
        f"id={event.id} tags={event.tags} payload={event.payload!r} meta={dict(event.meta)!r}"
    )
