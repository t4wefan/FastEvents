# FastEvents

**Bring the programming model of great Python web frameworks to general event systems.**

FastEvents is not just an event bus.

It is closer to an application model for events:

- organize behavior with declarative handlers
- build handler inputs through structured injection
- keep runtime complexity behind a clear runtime boundary
- compose higher-level protocols like RPC out of events
- leave room for streams, proxies, lifecycle events, and future distributed buses

If you like the feel of modern Python web frameworks - clear handlers, natural dependencies, explicit boundaries - FastEvents tries to bring that experience to more general event-driven systems.

---

## Install

If you want to use the repository directly:

```bash
uv add https://github.com/t4wefan/FastEvents.git
```

For local development:

```bash
uv sync
```

The current implementation targets Python `3.12+` and ships with an in-memory `InMemoryBus`.

---

## Quick Start

Start with the smallest possible hello world:

```python
from fastevents import FastEvents

app = FastEvents()

@app.on("hello")
async def hello() -> None:
    print("hello world")
```

Those four lines already show the basic shape of FastEvents:

- import `FastEvents`
- declare an `app`
- register a handler with `@app.on(...)`
- express event behavior with a normal async function

As you go further, FastEvents keeps building on top of that minimal model:

- `bus` owns the runtime boundary
- `app` declares behavior
- handlers stay small and explicit
- RPC is not a separate core model, but a protocol built on top of events

Here is a slightly richer example that shows:

- dependency injection
- `event.ctx.publish()`
- `pydantic` payload validation

```python
import asyncio

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus, RuntimeEvent, dependency

app = FastEvents()
bus = InMemoryBus()


class WorldPayload(BaseModel):
    text: str


@dependency
def say_hello(event: RuntimeEvent) -> bool:
    return event.payload is not None


@app.on("hello")
async def hello(event: RuntimeEvent, ok: bool = say_hello()) -> None:
    if ok:
        await event.ctx.publish(tags="world", payload={"text": "world"})


@app.on("world")
async def world(data: WorldPayload) -> None:
    print(data.text)


async def main() -> None:
    await bus.astart(app)
    try:
        await app.publish(tags="hello", payload={"message": "hello"})
    finally:
        await bus.astop()


asyncio.run(main())
```

What happens here:

- the `hello` handler receives the result of `say_hello()` through dependency injection
- `say_hello()` depends on the current `event` and returns `True` when the payload is not empty
- the `hello` handler publishes a follow-up `world` event when the condition is met
- the `world` handler validates the payload with a `pydantic` model and prints `world`

---

## Why It Is More Than Another Event Bus

Many event libraries stay close to transport or callback registries.

FastEvents is trying to solve a different problem:

**How do you write event applications the way you write web applications?**

In other words, the focus is not just "how do I send a message", but:

- how to clearly declare who handles which event
- how to inject payload, context, and dependencies naturally into handlers
- how to keep follow-up events, request/reply, and fallback inside one model
- how to keep runtime complexity in the bus instead of leaking it into the app layer

The goal is not to cram every feature into the core. The goal is to keep the core boundaries clear enough that higher-level protocols can grow naturally.

---

## Design Boundaries

The appeal of FastEvents is not only that it works, but that it stays disciplined about semantics.

### 1. `publish()` is a transport boundary, not a completion guarantee

Publishing an event means submitting it to the bus.

`publish()` does **not** guarantee that:

- any handler has already run
- any handler will succeed
- all processing has completed
- a distributed network has reached global consistency

It only guarantees this:

**the event has been accepted by the bus and entered the bus send boundary.**

This is intentional. It means the semantics of `publish()` do not need to change later, whether the runtime is an in-memory bus, a remote broker, or a distributed bus.

If you need replies, completion, aggregation, or acknowledgements, they should be built on top of events rather than pushed down into `publish()`.

### 2. `app` is not a full host

`FastEvents` is a declaration and composition container.

It is not a full application host, it does not own every resource, and it does not try to become a framework-managed plugin platform. Runtime complexity belongs to the bus, not to the app.

### 3. extensions are composition, not framework-managed plugins

Extensions should be ordinary objects that compose higher-level behavior from the app's public capabilities.

- they should not depend on framework internals
- they should not have privileged status
- their resource ownership should stay explicit

The recommended direction is explicit construction:

```python
from fastevents import FastEvents, RpcExtension

app = FastEvents()
rpc = RpcExtension(app)
```

This explicit composition style is clearer and works better with type inference.

### 4. runtime complexity belongs in the bus

The bus is responsible for:

- accepting published events
- binding to or creating an event loop
- managing runtime lifecycle
- transporting events into the application core
- absorbing future remote transport or distributed delivery complexity

That complexity belongs to the runtime layer. It should not leak into the application model.

---

## A Simple Mental Model

You can think about FastEvents as three layers.

### Runtime layer: bus

The `bus` is the lowest runtime boundary.

It decides how events are accepted, how runtime starts, who owns the loop, and how events enter the application core.

### Application core: dispatcher + injector

The application core turns an event into a concrete application call:

- `dispatcher` selects matching subscribers
- `injector` builds handler inputs from payload, context, and dependencies
- then the actual call runs

### Top layer: handlers + extensions

- `handler` expresses business behavior
- `extension` composes higher-level protocols, such as RPC, from public capabilities

Both layers are built on the same application core rather than inventing separate models.

---

## Propagation Model

FastEvents uses level-based propagation.

Intuitively:

- lower levels run first
- subscribers in the same level run concurrently
- `level < 0` is for observation and never consumes the event
- `level >= 0` is for handling and fallback

More concretely:

- the dispatcher finds all matching subscribers
- subscribers are grouped by `level`
- each group runs concurrently
- if a non-negative `level` consumes the event, higher levels stop
- if that level does not consume the event, propagation continues upward

Here, `consumed` is a propagation concept, not a broad business-success signal.

A common convention is:

- `-1`: audit, metrics, tracing, passive observers
- `0`: primary business handler
- `1+`: fallback

---

## Handler Injection

FastEvents supports structured parameter injection. You declare a handler signature, and the framework builds the current inputs for you.

Common forms:

- `RuntimeEvent`: get the current event
- `dict`: get the raw payload
- `pydantic.BaseModel`: get validated structured payload

Example:

```python
from pydantic import BaseModel
from fastevents import RuntimeEvent


class OrderCreated(BaseModel):
    order_id: int


@app.on("order.created")
async def event_only(event: RuntimeEvent) -> None:
    ...


@app.on("order.created")
async def raw_payload(payload: dict) -> None:
    ...


@app.on("order.created")
async def typed_payload(event: RuntimeEvent, data: OrderCreated) -> None:
    ...
```

If `pydantic` validation fails, the current handler is treated as declining the event, and propagation may continue to a higher level.

---

## Dependency Injection

Besides payload and event context, FastEvents also supports lightweight function-style dependencies:

```python
from fastevents import EventContext, RuntimeEvent, dependency


@dependency
def order_ctx(event: RuntimeEvent, ctx: EventContext) -> tuple[str, bool]:
    return (event.id, ctx is event.ctx)


@app.on("order.created")
async def handle(info=order_ctx()) -> None:
    print(info)
```

Dependencies can themselves depend on:

- the current event
- `ctx`
- payload injection
- other dependencies

Resolution is cached for one event dispatch, so multiple handlers handling the same event can reuse the same dependency result.

---

## `SessionNotConsumed` and Fallback

If a primary handler wants to explicitly decline handling, it can raise `SessionNotConsumed`.

This allows propagation to continue to a higher `level`, which makes fallback straightforward:

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

---

## `RuntimeEvent` and `ctx`

If a handler needs to keep talking to the bus - for example by publishing a follow-up event - it does so through `event.ctx`.

The most common capability is:

- `await event.ctx.publish(...)`

Example:

```python
@app.on("order.created")
async def handle(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.publish(tags="order.validated", payload=payload)
```

This means handlers do not need to carry a direct bus reference, and runtime interaction stays behind a clear boundary.

---

## Bus Lifecycle

In async environments:

```python
await bus.astart(app)
try:
    ...
finally:
    await bus.astop()
```

In synchronous environments:

```python
bus.start(app)
try:
    ...
finally:
    bus.stop()
```

You can also use `bus.run(app)` for a blocking runtime loop.

Before the bus starts, calling `publish()`, or calling `app.publish()` / `app.listen()` before the app is bound to a running bus, raises `BusNotStartedError`.

---

## Listen

`listen()` lives on `app` and creates a temporary stream subscriber:

```python
async with app.listen("notification.sent", level=-1) as stream:
    async for event in stream:
        print(event.payload)
```

This is useful when you want to observe a class of events at runtime without turning it into a permanent handler.

---

## RPC

RPC is not the core model of FastEvents.

It is a higher-level protocol built on top of events.

The current RPC extension provides:

- `request_stream()`
- `request_one()`
- `request()`
- `rpc_context()`

You can import the first-party RPC surface directly from [`fastevents/__init__.py`](fastevents/__init__.py).

These helpers build request/reply behavior on top of ordinary events without changing the core event semantics.

---

## Minimal Example

If you only want the smallest publish example:

```python
import asyncio

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus

app = FastEvents()
bus = InMemoryBus()


class Message(BaseModel):
    text: str


@app.on("message")
async def handle_message(payload: Message) -> None:
    print(f"got: {payload.text}")


async def main() -> None:
    await bus.astart(app)
    try:
        await app.publish(tags="message", payload=Message(text="hello"))
    finally:
        await bus.astop()


asyncio.run(main())
```

---

## A More Realistic Example

`demo.py` is closer to the real direction of the project. It combines:

- dependency injection
- streamed token events
- `ctx.publish()`
- RPC request/reply
- interactive terminal behavior

If you want to understand how FastEvents feels in a more realistic application, run it directly:

```bash
uv run python demo.py
```

---

## Current Status

FastEvents is evolving toward a clearer event-application model with stricter boundaries.

If you are evaluating it, the most important things to understand are:

- `publish()` intentionally has weak guarantees
- the bus owns runtime complexity
- the app is for declaration and composition
- extensions are explicit composition objects, not managed plugins
- higher-level protocols should be built on top of events rather than pushed into the core

---

## Philosophy in One Sentence

FastEvents takes the application model already proven in Python web frameworks, generalizes it from special events like HTTP to general event systems, and keeps the boundaries explicit enough to stay composable, extensible, and evolvable.
