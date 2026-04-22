from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel as PydanticBaseModel

if TYPE_CHECKING:
    from .subscribers import Dependency

class EventModel(PydanticBaseModel):
    """First-party pydantic model base recommended for payload injection."""

    @classmethod
    def _provider(cls) -> "Dependency[EventModel]":
        from .events import RuntimeEvent
        from .subscribers import dependency

        @dependency
        def provider(event: RuntimeEvent) -> EventModel:
            return cls.model_validate(event.payload)

        return provider
