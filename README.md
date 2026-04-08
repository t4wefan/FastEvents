## FastEvents

`FastEvents` is a lightweight asyncio event framework for applications that need more structure than plain pub/sub, but do not want a full workflow engine or message broker.

It focuses on a small set of ideas:

- layered propagation with `level`
- explicit primary-handler and fallback flows
- temporary event streams with `listen()`
- formal request/reply instead of collecting handler return values
- a minimal runtime event context with `publish()` and `reply()`

The current implementation targets Python `3.12+` and ships with an in-memory bus.

## Install

```bash
uv add fastevents
```

Or for local development:

```bash
uv sync
```

## Quick Start

```python
from __future__ import annotations

import asyncio

from fastevents import FastEvents, InMemoryBus, RuntimeEvent


app = FastEvents()
bus = InMemoryBus()


@app.on("user.lookup")
async def lookup(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.reply(payload={"user_id": payload["user_id"], "name": "Alice"})


async def main() -> None:
    await bus.astart(app)
    try:
        reply = await bus.request(tags="user.lookup", payload={"user_id": 7}, timeout=1)
        print(reply.payload)
    finally:
        await bus.astop()


asyncio.run(main())
```

## Core Model

There are two main runtime objects:

- `FastEvents`: declaration facade used to register subscribers
- `InMemoryBus`: runtime object that starts the app and exposes `publish()`, `listen()`, and `request()`

Typical setup:

```python
app = FastEvents()
bus = InMemoryBus()
```

Register subscribers with `@app.on(...)`, then start the bus.

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

Subscriptions support a compact tag DSL:

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

`level` is the main propagation control mechanism.

- `level < 0`: observation-only layers
- `level >= 0`: handling and fallback layers

Propagation rules:

- subscribers are grouped by level
- lower levels run before higher levels
- subscribers in the same level run concurrently
- negative levels never consume the event
- if any subscriber in a non-negative level returns `consumed=True`, higher levels do not run
- if all subscribers in a non-negative level return `consumed=False`, propagation continues upward

Typical convention:

- `-1`: audit, tracing, metrics, passive observers
- `0`: primary business handler
- `1+`: fallback handlers

## Handler Injection

The current v0 injection model is intentionally minimal.

Supported handler parameters:

- one event parameter annotated as `RuntimeEvent` or `StandardEvent`
- one payload parameter annotated as `dict`
- one payload parameter annotated as a `pydantic.BaseModel` subclass

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

Recoverable payload mismatches can fall through to a higher-level handler.

## Fallback with `SessionNotConsumed`

For non-negative handler subscribers, raising `SessionNotConsumed` means:

- this subscriber declines to claim the event
- higher levels may still run

Example:

```python
from fastevents import SessionNotConsumed


@app.on("user.lookup", level=0)
async def primary(data: dict) -> None:
    if data.get("source") == "legacy":
        raise SessionNotConsumed()


@app.on("user.lookup", level=1)
async def fallback(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.reply(payload={"path": "fallback", **payload})
```

## RuntimeEvent and `ctx`

Subscribers receive a runtime event view. The runtime capability surface lives on `event.ctx`.

Current runtime methods:

- `await event.ctx.publish(...)`
- `await event.ctx.reply(...)`

Use `publish()` to continue the flow with a new event:

```python
@app.on("order.created")
async def handle(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.publish(tags="order.validated", payload=payload)
```

Use `reply()` inside a request/reply flow:

```python
@app.on("user.lookup")
async def handle(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.reply(payload={"user_id": payload["user_id"], "name": "Alice"})
```

## Bus Lifecycle

Start and stop the bus explicitly:

```python
await bus.astart(app)
try:
    ...
finally:
    await bus.astop()
```

Or from synchronous code:

```python
bus.start(app)
try:
    ...
finally:
    bus.stop()
```

There is also `bus.run(app)` for a blocking runtime loop.

Before start, `publish()`, `listen()`, and `request()` raise `BusNotStartedError`.

## Publish

```python
await bus.publish(tags="order.created", payload={"order_id": 1})
```

Important semantic note: `publish()` only guarantees that the event has been created and accepted by the bus send boundary.

It does not guarantee that:

- dispatch already finished
- subscribers already succeeded
- a later reply already exists

That boundary is deliberate and matches the current RFC.

## Listen

`listen()` creates a temporary stream subscriber and returns an async context manager.

```python
async with bus.listen("notification.sent", level=-1) as stream:
    async for event in stream:
        print(event.payload)
```

Useful for:

- temporary observation tools
- UI streams
- tests and demos
- internal request/reply plumbing

## Request / Reply

`request()` is the standard single-reply API.

```python
reply = await bus.request(
    tags="user.lookup",
    payload={"user_id": 7},
    timeout=1,
)
```

Flow:

1. create a temporary reply subscriber
2. register it
3. publish the request event
4. wait for the first matching reply
5. always clean up the temporary subscriber

Reserved metadata keys:

- `reply_tags`
- `correlation_id`

`event.ctx.reply()` automatically uses those values when available.

If no reply arrives before the timeout, `RequestTimeoutError` is raised.

## Examples

- `python main.py`: smoke-style end-to-end example
- `python demo.py`: layered order workflow demo
- `python ai_api_demo.py`: run the FastAPI demo service

For implementation details and design rationale, see `rfc.md`.
