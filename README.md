# FastEvents

**FastEvents brings the programming model of great Python web frameworks to general event systems.**

Build applications around events with:

- a clear runtime boundary
- declarative handlers
- structured dependency injection
- composable higher-level protocols like RPC
- room for streams, proxies, lifecycle events, and future distributed buses

FastEvents is not just an event bus. It is an event application framework: one that takes the design lessons proven in HTTP frameworks and generalizes them to broader event-driven systems.

## Install

```bash
pip install fastevents
```

---

## Quick start

```python
import asyncio

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus
from fastevents.ext.rpc import RpcExtension, rpc_context, RpcContext

app = FastEvents()
rpc = RpcExtension(app)
bus = InMemoryBus()


class ChatTurn(BaseModel):
    text: str


class ChatReply(BaseModel):
    answer: str


@app.on("chat.request")
async def chat(payload: ChatTurn, rpc: RpcContext = rpc_context()) -> None:
    answer = f"You said: {payload.text}"
    await rpc.reply(payload=ChatReply(answer=answer))


async def main() -> None:
    await bus.astart(app)
    reply = await rpc.request_one(
        tags="chat.request",
        payload=ChatTurn(text="hello"),
        model=ChatReply,
    )
    print(reply.answer)


asyncio.run(main())
```

Output:

```text
You said: hello
```

This example already shows the core shape of FastEvents:

- `bus` owns the runtime boundary
- `app` declares behavior
- handlers stay small and explicit
- RPC is an extension built on top of events, not a separate core model

---

## Design goals

FastEvents is designed around a few strict boundaries.

### 1. `publish()` is a transport boundary, not a completion guarantee

Publishing an event means submitting it to the bus so matching handlers can respond.

`publish()` does **not** guarantee that:

- any handler has already run
- any handler will succeed
- processing has completed
- a distributed network has reached global consistency

It only guarantees that the event has been accepted and sent through the bus boundary.

This is intentional. If you need replies, completion, aggregation, or acknowledgements, build those as higher-level protocols on top of events.

### 2. `app` is not a full host

`FastEvents` is an application declaration and composition container.

It does not own all resources, does not imply a full plugin host, and does not replace the runtime responsibilities of the bus.

### 3. extensions are composition, not framework-managed plugins

Extensions are ordinary objects that use the app's public capabilities.

They are not privileged subsystems and should not depend on framework internals. Resource ownership remains explicit.

### 4. runtime complexity belongs in the bus

The bus may manage lifecycle, event loop ownership, transport concerns, and eventually distributed delivery behavior.

That complexity belongs in the runtime layer, not in the application model.

---

## Core model

FastEvents can be understood as a layered architecture.

### Runtime layer

#### bus

The bus is the lowest layer.

It is responsible for runtime and transport concerns, such as:

- accepting published events
- attaching to or creating an event loop
- managing runtime lifecycle
- transporting events into the application core
- supporting future distributed or multi-node delivery models

### Application core

The app core can be understood as a processing chain.

#### dispatcher

Selects matching responders and applies propagation rules.

#### injector

Builds the inputs for a call from payloads, context, and declared dependencies.

#### invoker

Performs the actual call.

### Top layer

#### handlers

Handlers express application behavior.

#### extensions

Extensions express higher-level capabilities, such as RPC or proxies, using only the app's public model.

Handlers and extensions are parallel layers built on top of the same application core.

---

## Propagation model

FastEvents uses a level-based propagation model.

The important idea is that propagation decisions come from clear stages in the application chain.

- The **dispatcher** decides which responders are candidates.
- The **injector** decides whether a valid call can be formed.
- The **handler** decides whether it accepts handling.

This keeps propagation semantics explicit without introducing a more complex state machine.

`consumed` is a propagation concept. It should be understood as whether higher-level propagation should continue, not as a broad business-success signal.

---

## Event loops and startup

FastEvents supports two runtime startup modes.

### `await bus.astart(app)`

Use this when you are already inside an existing event loop.

The bus binds its runtime to the current loop.

### `loop = bus.start(app)`

Use this from synchronous environments.

The bus creates and owns a new event loop and returns it.

This makes it possible to continue doing other synchronous setup or loop-bound work in that runtime.

In other words:

- use `astart()` when a loop already exists
- use `start()` when the bus should create the runtime loop

---

## Lifecycle as events

FastEvents treats lifecycle as part of the event model.

Runtime stages such as startup and shutdown can be represented as internal events rather than as a separate hook system.

This keeps lifecycle behavior inside the same programming model:

- the bus drives lifecycle transitions
- the app core handles lifecycle events using the same dispatch/injection/invocation pipeline
- handlers and extensions can respond without requiring a separate plugin lifecycle API

---

## Extensions

FastEvents extensions are built through explicit composition.

Recommended style:

```python
from fastevents import FastEvents
from fastevents.ext.rpc import RpcExtension

app = FastEvents()
rpc = RpcExtension(app)
```

This keeps extension construction explicit and improves clarity.

The historical `app.ex` style may still exist in some code paths, but explicit construction is the preferred direction.

### RPC

RPC is not the core model of FastEvents.

It is a higher-level protocol built on top of events.

The RPC extension provides helpers such as:

- `request_stream()`
- `request_one()`
- `request()`
- `rpc_context()`

These build request/reply behavior without changing the core event semantics.

### Proxies

A proxy is not a bus extension.

A proxy is a boundary adapter that can:

- maintain connections to external systems
- translate external events into internal events
- forward internal events outward when appropriate

This makes proxies part of the extension ecosystem rather than part of the transport core.

---

## Why FastEvents exists

Modern Python web frameworks have already established an excellent application programming model for one special class of events: HTTP.

FastEvents generalizes that model into a broader event-driven form.

It keeps the strengths of that ecosystem:

- clear runtime/application boundaries
- declarative handlers
- structured dependency injection
- explicit lifecycle
- composable higher-level protocols

And it extends them with the flexibility of general event systems.

---

## Minimal example

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
    await app.publish(tags="message", payload=Message(text="hello"))


asyncio.run(main())
```

---

## A richer example

The demo shows a more realistic composition of:

- dependency injection
- streamed token events
- `ctx.publish()`
- RPC request/reply
- interactive terminal behavior

That example is a better representation of the current direction of the project than a simple one-shot publish demo.

---

## Current status

FastEvents is evolving toward a clearer event-application model with stricter boundaries between runtime, application semantics, and extensions.

If you are evaluating the project, the most important things to understand are:

- `publish()` has intentionally weak guarantees
- the bus owns runtime concerns
- the app defines declaration and composition
- extensions are explicit composition objects, not managed plugins
- higher-level protocols should be built on top of events rather than pushed into the core model

---

## Philosophy in one sentence

FastEvents takes the application model proven by Python web frameworks, generalizes it from special events to general events, and keeps the boundaries explicit enough to remain composable.

