from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from .dispatcher import Dispatcher
from .subscribers import HandlerSubscriber
from .subscription import SubscriptionInput


class FastEvents:
    """Declaration facade used to register event subscribers."""

    def __init__(self) -> None:
        self.dispatcher = Dispatcher(runtime_publisher=None)

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
