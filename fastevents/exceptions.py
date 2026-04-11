class FastEventsError(Exception):
    """Base exception for the package."""


class BusNotStartedError(FastEventsError):
    """Raised when a runtime API is used before the bus is started."""


class BusAlreadyStartedError(FastEventsError):
    """Raised when start/run is called on a started bus."""


class InjectionError(FastEventsError):
    """Raised when a handler signature cannot be satisfied."""


class SessionNotConsumed(FastEventsError):
    """Control signal for handler subscribers to decline consumption."""

