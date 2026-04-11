from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .app import FastEvents
    from .ext.rpc import RpcExtension


class AppExtensions:
    """Mount namespace for app-bound extensions."""

    def __init__(self, app: FastEvents) -> None:
        object.__setattr__(self, "_app", app)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return

        binder = getattr(value, "bind_app", None)
        if callable(binder):
            binder(object.__getattribute__(self, "_app"))
        object.__setattr__(self, name, value)

    def mount(self, name: str, extension: Any) -> Any:
        setattr(self, name, extension)
        return extension
