from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel as PydanticBaseModel

if TYPE_CHECKING:
    pass

class EventModel(PydanticBaseModel):
    """First-party pydantic model base recommended for payload injection."""
