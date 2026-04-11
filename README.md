## FastEvents

`FastEvents` is a lightweight, general-purpose Python `asyncio` event bus with a clear event model, a small runtime, and an extensible bus abstraction.

With just a few lines of code, you can try event-driven programming directly in Python:

```python
from fastevents import FastEvents, RuntimeEvent

app = FastEvents()

@app.on("hello")
async def hello(event: RuntimeEvent) -> None:
    print("hello world")
```

The current implementation targets Python `3.12+` and ships with an in-memory bus.

## Install

```bash
uv add https://github.com/t4wefan/FastEvents.git
```

Or for local development:

```bash
uv sync
```

## Quick Start

First define an app and register an event handler:

```python
import asyncio
from fastevents import FastEvents, InMemoryBus, RuntimeEvent

app = FastEvents()

@app.on("hello")
async def hello(event: RuntimeEvent) -> None:
    print("hello world")
```

Then start the bus and publish an event:

```python
bus = InMemoryBus()

async def main() -> None:
    await bus.astart(app)
    try:
        await bus.publish(tags="hello")
    finally:
        await bus.astop()

asyncio.run(main())
```

That is the basic usage model:

- create an app with `FastEvents()`
- register handlers with `@app.on(...)`
- start the runtime with `InMemoryBus()` and publish events

## Core Model

The two most common objects are:

- `FastEvents`: declaration facade used to register subscribers
- `InMemoryBus`: runtime object that starts the app and exposes `publish()`

Typical setup:

```python
app = FastEvents()
bus = InMemoryBus()
```

Register subscribers with `@app.on(...)`. Temporary listening and app-bound publishing are then available through `app.listen()` and `app.publish()`.

## Event Model

A standard event contains:

- `id`
- `timestamp`
- `tags`
- `meta`
- `payload`

Tags are normalized to lowercase, deduplicated, and sorted. Allowed tag characters are letters, digits, `_`, and `.`.

Examples:

- `"order.created"`
- `("order.submitted", "vip")`
- `["payment.failed", "high_value"]`

## Subscription DSL

Subscriptions support a compact tag DSL for combining multiple tags with simple logic:

- `"order.created"`: match a single tag
- `("order.submitted", "vip")`: all patterns in the tuple must match
- `["ops.alert", ("payment.failed", "high_value")]`: OR across clauses
- `("order.submitted", "-legacy")`: require `order.submitted` and exclude `legacy`
- `"order.*"`: wildcard match using `fnmatch`

Examples:

```python
@app.on("order.created")
async def handle_created(event: RuntimeEvent) -> None:
    ...


@app.on(("order.submitted", "vip"))
async def handle_vip(event: RuntimeEvent) -> None:
    ...


@app.on(["ops.alert", ("payment.failed", "high_value")])
async def handle_ops(event: RuntimeEvent) -> None:
    ...
```

## Levels and Propagation

We use `level` to control how events move across handlers.

- `level < 0`: observation-only layers
- `level >= 0`: handling and fallback layers

Propagation rules:

- subscribers are grouped by level
- lower levels run before higher levels
- subscribers in the same level run concurrently
- negative levels never consume the event
- if any subscriber in a non-negative level returns `consumed=True`, higher levels do not run
- if all subscribers in a non-negative level return `consumed=False`, propagation continues upward

Default handler registration uses `level=0`.

Typical convention:

- `-1`: audit, tracing, metrics, passive observers
- `0`: primary business handler
- `1+`: fallback handlers

## Handler Injection

FastEvents supports automatic injection for common handler parameters. You declare them with type annotations, and the framework provides the current event or payload.

Typical forms are:

- use `RuntimeEvent` to access the current event
- use `dict` to access the raw payload
- use a `pydantic.BaseModel` subclass to receive validated structured data

Examples:

```python
from pydantic import BaseModel
from fastevents import RuntimeEvent


class OrderCreated(BaseModel):
    order_id: int


@app.on("order.created")
async def event_only(event: RuntimeEvent) -> None:
    ...


@app.on("order.created")
async def dict_payload(payload: dict) -> None:
    ...


@app.on("order.created")
async def typed_payload(event: RuntimeEvent, data: OrderCreated) -> None:
    ...
```

If payload validation fails for a `pydantic` model, the current handler is treated as having declined the event, and a higher-level handler can still run.

Basic function-style dependencies are also supported:

```python
from fastevents import EventContext, RuntimeEvent, dependency


@dependency
def order_ctx(event: RuntimeEvent, ctx: EventContext) -> tuple[str, bool]:
    return (event.id, ctx is event.ctx)


@app.on("order.created")
async def handle(info=order_ctx()) -> None:
    print(info)
```

Dependencies can depend on the current event, `ctx`, payload injection, or
other dependencies. Resolution is cached for the current event dispatch, so
multiple subscribers handling the same event can reuse the same dependency
result.

## Fallback with `SessionNotConsumed`

If a handler wants to explicitly give up processing, it can raise `SessionNotConsumed`. The event will then continue to a higher `level`, which makes fallback handlers straightforward to express.

Example:

```python
from fastevents import SessionNotConsumed


@app.on("user.lookup", level=0)
async def primary(data: dict) -> None:
    if data.get("source") == "legacy":
        raise SessionNotConsumed()


@app.on("user.lookup", level=1)
async def fallback(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.publish(tags="user.lookup.fallback", payload={"path": "fallback", **payload})
```

## RuntimeEvent and `ctx`

Inside a handler, if you need to keep talking to the bus - for example by publishing a follow-up event - use `event.ctx`.

The most common method is:

- `await event.ctx.publish(...)`

Use `publish()` to continue the flow with a new event:

```python
@app.on("order.created")
async def handle(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.publish(tags="order.validated", payload=payload)
```

## Bus Lifecycle

The bus can be started asynchronously:

```python
await bus.astart(app)
try:
    ...
finally:
    await bus.astop()
```

From synchronous code, you can also use:

```python
bus.start(app)
try:
    ...
finally:
    bus.stop()
```

There is also `bus.run(app)` for a blocking runtime loop. In practice, that means the bus takes over the main thread until it stops.

Before start, calling `publish()`, or calling `app.publish()` / `listen()` before the app is bound to a running bus, raises `BusNotStartedError`.

## Publish

```python
await bus.publish(tags="order.created", payload={"order_id": 1})
```

`publish()` sends an event, but it only guarantees that the event has been created and accepted by the bus.

If you want to publish through the app boundary instead of calling the runtime directly:

```python
await app.publish(tags="order.created", payload={"order_id": 1})
```

## Listen

`listen()` lives on `app` and creates a temporary stream subscriber.

```python
async with app.listen("notification.sent", level=-1) as stream:
    async for event in stream:
        print(event.payload)
```

This is useful when you want to observe a class of events at runtime without turning it into a permanent handler.

## Examples

- `python demo.py`: simple streaming terminal demo

For implementation details and design rationale, see `docs/rfc.md`.
