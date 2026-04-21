RFC 0001: fastevents Core Model and Runtime Protocol

Status: Draft
Authors: fan / collaborative edit
Target: v0 / first implementable core
Scope: core abstractions, propagation semantics, user API, runtime protocol
Non-goals: distributed consistency, persistence semantics, transport-specific details, full DI system

---

1. Summary

fastevents is a lightweight asynchronous event framework running on the `asyncio` event loop.
It centers business semantics in `Dispatcher`, runtime lifecycle and I/O in `Bus`, unified event participants in `Subscriber`, layered propagation through `level`, and formal result delivery through `request/reply`.

fastevents is not a message queue platform and not a workflow orchestrator. Its goal is to give medium and small asynchronous systems with non-trivial control flow a single, consistent way to express:

- events
- layered propagation
- subscribers
- fallback
- request/reply
- bus-driven runtime

Typical fit:

- stage-based business pipelines
- protocol message handling
- UI / agent / tool style async callback convergence
- systems that need logging / tracing / audit / primary handling / fallback separation

This RFC defines a version that engineers can implement directly. Where the previous draft left room for interpretation, this version chooses explicit v0 behavior.

---

2. Design Goals

The framework is designed to solve these common problems:

1. Business flows degrade into nested `if/else`, early returns, and exception branches.
2. Callback chains grow without a clear propagation and fallback model.
3. Lightweight pub/sub cannot express "who observes, who handles first, who is fallback".
4. Systems that need results often degrade into fragile "collect handler return values" designs.
5. Teams want multiple buses or transports later without leaking business semantics into transport code.

The v0 design must preserve:

- few concepts
- direct API
- explicit implementation boundaries
- an in-memory first implementation path

---

3. Core Principles

3.1 Dispatcher-first semantics

`Dispatcher` owns matching, level grouping, subscriber invocation, propagation, and consumption decisions.

3.2 Bus owns runtime boundaries

`Bus` owns start/stop lifecycle, event construction for user-facing publish operations, inbound event ingestion, request setup, temporary stream registration, and the scheduling boundary between "accepted for sending" and "actually dispatched".

3.3 Reply-first results

Formal results are modeled by `request/reply`, not by collecting handler return values.

3.4 Level-first propagation

`level < 0` means observation-only layers.
`level >= 0` means handling and fallback layers.

3.5 Minimal injection

v0 does not define a general DI container.
Handler injection supports only:

- one event parameter
- one payload parameter: `pydantic.BaseModel` subclass or `dict`

3.6 Runtime capability is explicit

Subscribers receive `RuntimeEvent`, not bare `Event`.
Runtime capabilities are exposed only through `event.ctx`.

---

4. Scope and Non-goals

4.1 In scope

- standard event model
- runtime event model
- subscriber abstraction
- dispatcher propagation semantics
- handler injection and execution semantics
- stream-style temporary subscription
- bus lifecycle and user-facing API
- minimal request/reply protocol

4.2 Out of scope

- message persistence
- exactly-once / at-least-once guarantees
- distributed transactions
- transport-specific binding details
- broker-level routing optimization
- horizontal scaling strategy
- security and permissions
- complete tracing standardization

---

5. Terms

Event

Serializable event data with no runtime references.

RuntimeEvent

The event view passed into subscriber handling. It exposes the same data as `Event` plus `ctx`.

Subscriber

Unified dispatch participant. It decides whether it should participate and returns a normalized handling result.

Dispatcher

Semantic core. It matches, groups by level, invokes subscribers, and decides whether propagation continues.

Bus

Runtime and connection layer. It runs the app, accepts publish/request/listen calls, and ingests external events.

Level

Subscriber layer used to determine execution order and cross-layer stopping behavior.

Consumed

The event has been claimed by a subscriber or by a whole non-negative layer, so higher levels must not run.

Fallback

Propagation to a higher level when the current non-negative level does not consume the event.

Request / Reply

Formal result protocol built on top of events. A reply is another published event following reserved metadata rules.

---

6. Event Model

6.1 Standard Event

`Event` is a pure data object and must not contain runtime objects.

Recommended protocol:

```python
from typing import Any, Mapping, Protocol


class Event(Protocol):
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
```

Field semantics:

- `id: str`
  Unique event identifier for debugging, correlation, logging, and tracing.
- `timestamp: float`
  UTC Unix timestamp. v0 uses numeric timestamps instead of `datetime` to minimize cross-boundary serialization complexity.
- `tags: tuple[str, ...]`
  Canonical tags used by subscription matching and propagation.
- `meta: Mapping[str, Any]`
  Extensible metadata. v0 reserves a small set of keys for request/reply.
- `payload: Any`
  Business payload. v0 does not impose a global payload schema.

6.2 Read-only semantics

Although implementations may internally use mutable objects such as `dict`, all subscribers must treat `Event` as read-only.

Subscribers must not rely on in-place mutation of:

- `tags`
- `meta`
- `payload`

to influence sibling subscribers or later propagation.

Reasons:

1. Same-level subscribers may run concurrently.
2. Stream and handler subscribers may observe the same event concurrently.
3. In-place mutation makes propagation behavior difficult to reason about.

To move state forward, publish a new event.

6.3 Tag normalization

`tags` accepts a single string as sugar:

- `tags="order.created"` is equivalent to `tags=["order.created"]`

Normalization rules for v0:

1. Matching is case-insensitive.
2. Implementations must normalize tags to lowercase.
3. Allowed characters are letters, digits, `_`, and `.`.
4. Duplicate tags are removed.
5. Final representation is an immutable `tuple[str, ...]`.
6. v0 requires deterministic output. The recommended strategy is deduplicate + sort.

---

7. Runtime Event Model

7.1 RuntimeEvent

Subscribers receive `RuntimeEvent` during handling.

```python
from typing import Protocol


class RuntimeEvent(Event, Protocol):
    @property
    def ctx(self) -> "EventContext": ...
```

`RuntimeEvent` is a handling-time view, not a wire format.

- `Event` may cross process or transport boundaries.
- `RuntimeEvent` is constructed when the dispatcher crosses into subscriber handling.

7.2 RuntimeEvent construction rule

For one dispatch operation, v0 treats the underlying event data as a single immutable logical event.
Implementations may reuse one wrapper instance or create multiple wrappers, but observable behavior must be equivalent:

- all subscribers see identical event data
- all subscribers receive a valid `ctx`
- no subscriber can mutate the runtime wrapper in a way that changes what another subscriber sees

7.3 EventContext

`RuntimeEvent.ctx` exposes a minimal runtime capability surface.

v0 only includes:

- `publish()`
- `reply()`

```python
from typing import Any, Protocol


class EventContext(Protocol):
    async def publish(
        self,
        *,
        tags: "TagInput",
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> Event: ...

    async def reply(
        self,
        payload: Any = None,
        *,
        tags: "TagInput" | None = None,
        meta: dict[str, Any] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> Event: ...
```

7.4 Why `ctx` does not include `request()` or `listen()`

`publish()` and `reply()` only continue the event flow.
`request()` and `listen()` also create and manage subscribers and their lifecycles. Those are bus-level control capabilities, not minimal per-event runtime capabilities.

So in v0:

- `ctx.publish()` is allowed
- `ctx.reply()` is allowed
- `ctx.request()` is not included
- `ctx.listen()` is not included

7.5 `reply()` semantics

`reply()` is sugar over `publish()`.

Default behavior:

1. Use `event.meta["reply_tags"]` as target tags unless explicit `tags` is provided.
2. Copy `event.meta["correlation_id"]` into the reply metadata when present.
3. Start reply metadata from an empty dict.
4. Only reserved reply-chain keys are inherited automatically in v0.
5. Explicit `meta` is merged on top of the default reply metadata.

If both are true:

- current event has no `reply_tags`
- caller does not pass explicit `tags`

then `reply()` must raise `ReplyNotAvailableError`.

v0 explicitly does not inherit the entire incoming `meta` dict.

---

8. Subscription Expression

8.1 Basic form

Subscription conditions are expressed with native Python data structures instead of a custom DSL.

Rules:

- top level is OR
- a `tuple[...]` clause means AND
- an atomic item is one pattern

This is a lightweight DNF.

8.2 Examples

Single atom:

```python
"order.created"
```

AND clause:

```python
("order.*", "vip")
```

Meaning:

- at least one tag matches `order.*`
- and at least one tag matches `vip`

OR top level:

```python
["admin", ("order.*", "vip")]
```

8.3 Atomic patterns

v0 supports two atomic pattern types.

Positive pattern examples:

- `"order.created"`
- `"order.*"`
- `"vip"`

Negative pattern example:

- `"-test"`

Meaning:

- no event tag matches `test`

Because valid tag characters do not include `-`, the leading `-` prefix is reserved for negative patterns and does not conflict with valid tag syntax.

8.4 Sugar

Single-string subscriptions are valid sugar:

- `@app.on("order.created")`
- `bus.listen("order.reply")`

Both mean a top-level OR with one atomic condition.

8.5 Ownership of matching logic

The dispatcher does not need to understand subscription expressions directly.
In v0, subscription expressions are a construction-time input for subscriber implementations. At dispatch time, the dispatcher only calls:

```python
subscriber.should_handle(event)
```

This keeps matching semantics encapsulated inside each subscriber.

---

9. Subscriber

9.1 Definition

`Subscriber` is the unified participant interface for dispatch.

It provides two core capabilities to the dispatcher:

1. `should_handle(event)`: whether the subscriber participates in this dispatch decision
2. `handle(event)`: perform handling and return a normalized `SubscriberResult`

The dispatcher must not depend on subscriber internals such as:

- handler-style vs stream-style
- injector / invoker existence
- internal queueing
- fan-out composition

9.2 Protocol

```python
from typing import Protocol


class Subscriber(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str | None: ...

    @property
    def level(self) -> int: ...

    @property
    def closed(self) -> bool: ...

    def should_handle(self, event: Event) -> bool: ...

    async def handle(self, event: RuntimeEvent) -> "SubscriberResult": ...

    async def close(self) -> None: ...
```

9.3 Field semantics

- `id`
  Unique subscriber identity. Dispatcher registry keys by `id`.
- `name`
  Human-readable label for logging, debugging, snapshotting, and error reporting.
- `level`
  Propagation layer.
- `closed`
  Closed subscribers do not participate in matching.

9.4 `should_handle(event)`

This method indicates whether the subscriber declares itself relevant before actual handling begins.

It may include:

- tag matching
- transient state checks
- lightweight custom filters

It does not mean handling will necessarily succeed.

9.5 `handle(event)`

`handle()` is the unified execution entry point. The dispatcher only cares about the returned `SubscriberResult`.

---

10. SubscriberResult

Recommended definition:

```python
from dataclasses import dataclass


@dataclass(slots=True)
class SubscriberResult:
    consumed: bool
    exc: BaseException | None = None
```

10.1 Semantics

- `consumed=False, exc=None`
  The subscriber did not consume the event. Propagation may continue to higher non-negative levels.
- `consumed=True, exc=None`
  The subscriber consumed the event successfully.
- `consumed=True, exc=...`
  The subscriber consumed the event but failed. This must not be downgraded into "not consumed".

10.2 Why a normalized result object exists

Without it, the dispatcher would need to interpret truthy returns, special exceptions, or subscriber-specific types. `SubscriberResult` separates subscriber internals from propagation decisions.

---

11. Propagation Rules

11.1 Dispatch algorithm

For one event dispatch, the dispatcher must:

1. Collect all subscribers where `closed is False` and `should_handle(event)` is true.
2. Group them by `level`.
3. Execute groups from lower level to higher level.
4. After each level completes, decide whether higher levels should run.

11.2 Negative levels: `level < 0`

For negative levels:

- all matched subscribers in the level run
- the level never consumes the event
- propagation always continues to the next level
- exceptions do not stop propagation
- exceptions must still be reported through the unified error outlet

Typical use:

- logging
- tracing
- metrics
- audit
- observation streams
- side-checks or preprocessing that must not claim the event

11.3 Non-negative levels: `level >= 0`

For non-negative levels:

- all matched subscribers in the same level run concurrently
- if any subscriber in the level returns `consumed=True`, the level consumes the event
- once a non-negative level consumes the event, no higher level runs
- if every subscriber in the level returns `consumed=False`, propagation continues upward

11.4 Same-level rule

Consumption only affects higher levels, never siblings in the current level.

That means:

- every matched subscriber in the current level runs
- even if one sibling consumes early, the dispatcher still waits for all siblings in the same level
- only after the whole level finishes does the dispatcher decide whether to stop

11.5 Error reporting during dispatch

Ordinary subscriber failures are not returned from `bus.publish()` in v0 because publish only waits for send-boundary acceptance.

Dispatcher must expose a unified observable error outlet.
The minimal required information for each failure report is:

- event
- subscriber
- exception

Recommended hook shape:

```python
async def error_hook(event: Event, subscriber: Subscriber, exc: BaseException) -> None: ...
```

If no custom hook is configured, the implementation must at least log the failure.

When multiple subscribers fail in one level, each failure is reported individually.

---

12. Handler-style Subscriber

12.1 Structure

A typical handler-style subscriber contains:

- user callback
- injector
- invoker

The dispatcher does not interact with those pieces directly.

12.2 Injection model

v0 supports a minimal injection model:

- at most one event parameter
- at most one payload parameter
- the payload parameter may be either:
  - a `pydantic.BaseModel` subclass
  - `dict`

Rules:

1. Event parameter appears at most once.
2. `BaseModel` and `dict` share one payload slot.
3. Payload slot appears at most once.
4. Any other required parameter is a signature error.

Valid examples:

```python
@app.on("order.created")
async def handle(event: Event): ...


@app.on("order.created")
async def handle(data: OrderCreated): ...


@app.on("order.created")
async def handle(event: Event, data: OrderCreated): ...


@app.on("order.created")
async def handle(event: Event, payload: dict): ...
```

Invalid examples:

```python
async def handle(a: Event, b: Event): ...


async def handle(a: OrderCreated, b: dict): ...


async def handle(x: int): ...
```

12.3 Recoverable injection failures

To support same-tag fallback across levels, some injection failures must be treated as recoverable adaptation failures.

Recoverable failures in v0:

- `pydantic.ValidationError`
- payload slot requires `dict` but `event.payload` is not a `dict`
- explicit framework `InjectionError`

Handler-style subscriber must catch those and return:

```python
SubscriberResult(consumed=False, exc=None)
```

Meaning: this handler does not fit the event, but the event itself is not considered failed.

12.4 Non-recoverable errors

The following must remain real failures:

- injector implementation bugs
- impossible internal signature states
- framework internal errors
- invoker runtime errors
- business exceptions raised by the handler

12.5 `SessionNotConsumed`

`SessionNotConsumed` is a propagation control signal with narrow scope.

It has special meaning only when all are true:

- execution is inside a handler-style subscriber
- injection already succeeded
- execution is in the invoker / handler call phase
- subscriber level is non-negative

Meaning:

- the current subscriber explicitly declines consumption
- propagation may continue to higher levels

In every other place it is treated as a normal exception.

Important v0 rule:

- only handler-style subscriber implementation may interpret `SessionNotConsumed`
- dispatcher itself must not special-case this exception directly

12.6 Handler claim boundary

Handler-style execution has two phases.

Phase A: injector phase

- successful injection -> enter phase B
- recoverable injection failure -> `consumed=False`
- non-recoverable error -> real failure

Phase B: invoker / handler phase

- normal return -> `consumed=True`
- raise `SessionNotConsumed` -> `consumed=False`
- raise any other exception -> `consumed=True, exc=e`

Therefore the claim boundary is after successful injection, not before it.

This is what makes payload-schema fallback possible.

---

13. Stream-style Subscriber

13.1 Definition

Stream-style subscribers provide async event streams such as:

- `bus.listen(...)`
- the temporary reply listener used by `bus.request(...)`

They usually maintain an async queue and expose events through async iteration.

13.2 Queue policy

Default queue policy in v0:

- unbounded queue

Users may configure a bounded queue with `maxsize`.

The default is unbounded so enqueue is close to instantaneous and the stream consumption boundary stays clear.

13.3 Claim boundary

For a stream-style subscriber, the claim boundary is enqueue.

- enqueue succeeds -> handling succeeds for that subscriber
- enqueue fails -> `consumed=True, exc=...`

Stream-style subscribers do not support `SessionNotConsumed`.

If a user wants observation without consuming, they must use a negative level explicitly.

13.4 Stream session runtime errors

Errors raised while user code consumes the stream, for example:

```python
async with bus.listen(...) as sub:
    async for event in sub:
        ...
```

are stream session errors, not dispatch errors.

Their semantics:

- terminate the current stream session
- automatically reclaim the temporary subscriber
- propagate the exception outward

They do not retroactively change an already completed dispatch decision.

13.5 Public stream protocol

Internally, a stream may be implemented as a subscriber. Publicly, v0 recommends exposing a narrower stream-oriented protocol to users:

```python
from typing import AsyncIterator, Protocol


class EventStream(AsyncIterator[RuntimeEvent], Protocol):
    async def close(self) -> None: ...
```

`Bus.listen()` should return an async context manager that yields an `EventStream`-like object.

---

14. Dispatcher

14.1 Responsibilities

`Dispatcher` is the semantic core. It is responsible for:

- subscriber registry management
- `should_handle(event)` matching
- level grouping
- subscriber invocation
- propagation stop decisions
- snapshot export for bus/runtime observation

It is not responsible for:

- transport details
- bus lifecycle
- user-facing send boundary

14.2 Protocol

```python
from typing import Protocol


class Dispatcher(Protocol):
    def add_subscriber(self, subscriber: Subscriber) -> Subscriber: ...
    def remove_subscriber(self, subscriber_id: str) -> None: ...
    def snapshot(self) -> "DispatcherSnapshot": ...
    async def dispatch(self, event: Event) -> None: ...
```

v0 intentionally removes `Dispatcher.publish()` from the protocol.

14.3 Why dispatcher only exposes `dispatch()`

There is one important runtime boundary in v0:

- `Bus.publish()` means the event has been accepted into the bus send boundary
- `Dispatcher.dispatch()` means subscriber matching and propagation are actually executing

Owning both in `Bus` and `Dispatcher` would blur responsibilities. Therefore:

- `Bus` owns publish / send / ingest
- `Dispatcher` owns dispatch only

14.4 Error handling principle

Dispatcher only relies on normalized subscriber results and the unified error outlet.
It does not reinterpret ordinary subscriber failures as fallback.

Special propagation semantics are recognized inside subscriber implementations, not by dispatcher-wide exception magic.

---

15. DispatcherSnapshot

`DispatcherSnapshot` is a read-only runtime observation object.

v0 requires that a snapshot include enough information for debugging and bus-side synchronization decisions. The minimal subscriber entry should include:

- `id`
- `name`
- `level`
- `closed`

Implementations may include more fields, but the snapshot must be safe to read without mutating dispatcher state.

---

16. Bus

16.1 Role

`Bus` is the app runtime and connection layer.
It drives the app and dispatcher and exposes event I/O APIs.

Users are expected to hold and understand the bus explicitly.

16.2 User-facing role split

- `FastEvents` declares subscribers
- `Bus` runs the app and exposes runtime APIs

16.3 Protocol

```python
from typing import Any, AsyncContextManager, Protocol


class Bus(Protocol):
    def run(self, app: "FastEvents") -> None: ...
    def start(self, app: "FastEvents") -> None: ...
    async def astart(self, app: "FastEvents") -> None: ...
    def stop(self) -> None: ...
    async def astop(self) -> None: ...

    async def publish(
        self,
        *,
        tags: "TagInput",
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        id: str | None = None,
        timestamp: float | None = None,
    ) -> Event: ...

    def listen(
        self,
        subscription: "SubscriptionInput",
        *,
        level: int = 0,
        name: str | None = None,
        maxsize: int = 0,
    ) -> AsyncContextManager[EventStream]: ...

    async def request(
        self,
        *,
        tags: "TagInput",
        payload: Any = None,
        meta: dict[str, Any] | None = None,
        timeout: float | None = None,
        level: int = 0,
    ) -> Event: ...

    async def send(self, event: Event) -> None: ...
    async def ingest(self, event: Event) -> None: ...
    async def sync(self, snapshot: "DispatcherSnapshot") -> None: ...
```

16.4 Lifecycle semantics

`run(app)`

- binds app and dispatcher
- completes initialization
- enters blocking runtime execution
- returns only after stop or another terminal condition

`start(app)`

- binds app and dispatcher
- enters started state
- schedules required internal runtime mechanisms
- returns immediately

`stop()`

- requests shutdown
- blocks until required cleanup is complete
- returns only after shutdown is fully complete

v0 chooses a synchronous blocking `stop()` so users get a single, obvious shutdown boundary.

16.5 Start-state rules

- before start, `publish()`, `listen()`, and `request()` must raise `BusNotStartedError`
- calling `start()` or `run()` while already started must raise `BusAlreadyStartedError`

v0 may allow restarting after `stop()`, but the implementation must guarantee clean rebinding and consistent sync state.

16.6 Publish boundary

`await bus.publish(...)` guarantees only this:

- the event has been successfully constructed and accepted into the bus send boundary

It does not guarantee:

- dispatch already completed
- subscribers already succeeded
- a reply already arrived
- no later failure exists

16.7 `send()` and `ingest()`

For v0:

- `send(event)` means the bus accepts an already-built event into its outbound/runtime send boundary
- `ingest(event)` means the bus accepts an already-built event from an external or upstream boundary into local processing

Both remain bus responsibilities because they sit on runtime boundaries, not semantic dispatch boundaries.

16.8 `sync(snapshot)`

`sync(snapshot)` is a bus-facing extension point for non-memory implementations.
The in-memory v0 bus may implement it as a no-op or a minimal internal refresh step.

---

17. FastEvents

17.1 Role

`FastEvents` is the declaration facade used to register subscribers.
It is not the main runtime object.

17.2 Protocol

```python
from typing import Any, Callable, Protocol


class FastEvents(Protocol):
    @property
    def dispatcher(self) -> Dispatcher: ...

    def on(
        self,
        subscription: "SubscriptionInput",
        *,
        level: int = 0,
        name: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...
```

17.3 Why runtime APIs live on bus

The following are runtime-state operations and therefore belong on `Bus`:

- `publish()`
- `listen()`
- `request()`
- `run()`
- `start()`
- `stop()`

Role split is deliberate:

- app defines rules
- bus runs and controls the runtime

---

18. Request / Reply

18.1 `request()` overview

`request()` is the standard sugar for a single-reply interaction.

Internal flow:

1. create a temporary reply stream subscriber
2. register it to the dispatcher
3. publish the request event
4. wait for the first matching reply
5. always clean up the temporary subscriber

18.2 Default level

`request()` defaults to `level=0`.

18.3 Single-reply rule

v0 defines first-reply-wins semantics:

- only the first matching reply is returned
- after the first matching reply, the temporary subscriber is closed and removed immediately
- later replies are ignored

18.4 Reserved metadata keys

v0 reserves:

- `meta["reply_tags"]`
- `meta["correlation_id"]`

Meaning:

- `reply_tags`: tags that a reply should target
- `correlation_id`: request/reply chain identifier

18.5 Request-side obligations

When `request()` publishes the request event, it must write both reserved keys:

- `reply_tags`
- `correlation_id`

18.6 Reply-side obligations

When `event.ctx.reply()` is called without explicit override tags, it must read:

- `reply_tags`
- `correlation_id`

and publish the reply accordingly.

18.7 Reply matching rule

The temporary reply subscriber created by `request()` must match on both:

- target reply tags
- exact `correlation_id`

Matching on tags alone is not sufficient in v0 because concurrent requests could otherwise cross-deliver replies.

18.8 Cleanup rule

The temporary reply subscriber must be cleaned up in all cases:

- success
- timeout
- cancellation
- publish failure
- any internal request error

Implementations should enforce this with `try/finally`.

---

19. User API Shape

19.1 Main objects

```python
app = FastEvents()
bus = InMemoryBus()
```

- `app` declares subscribers
- `bus` runs the app and provides runtime APIs

19.2 Common operations

Register a handler:

```python
@app.on("order.created")
async def handle(event: Event):
    ...
```

Run the app:

```python
bus.run(app)
```

or:

```python
bus.start(app)
...
bus.stop()
```

Publish an event:

```python
await bus.publish(
    tags="order.created",
    payload={"order_id": 1},
)
```

Listen temporarily:

```python
async with bus.listen("order.reply") as stream:
    async for event in stream:
        ...
```

Request / reply:

```python
reply = await bus.request(
    tags="user.lookup",
    payload={"user_id": 1},
    timeout=3,
)
```

Continue the flow from a handler:

```python
@app.on("order.created")
async def handle(event: RuntimeEvent):
    await event.ctx.publish(
        tags="order.validated",
        payload=event.payload,
    )
```

Reply from a handler:

```python
@app.on("user.lookup")
async def handle(event: RuntimeEvent, data: UserLookup):
    await event.ctx.reply(
        payload={"user_id": data.user_id, "name": "Alice"},
    )
```

19.3 Default mental model

Default level is `0`.

This means that after an event is published, all same-level matching subscribers participate concurrently.
If a user wants observation without entering the default consumption chain, they should use a negative level explicitly.

---

20. Minimal Exceptions

This RFC references a small set of framework-level exceptions that v0 should define:

- `BusNotStartedError`
- `BusAlreadyStartedError`
- `ReplyNotAvailableError`
- `InjectionError`
- `SessionNotConsumed`

Other framework internal errors may exist, but the above set is part of the public semantic contract in v0.

---

21. Recommended First-page Example

```python
from pydantic import BaseModel


app = FastEvents()


class OrderCreated(BaseModel):
    order_id: int


@app.on("order.created")
async def handle(event: RuntimeEvent, data: OrderCreated):
    await event.ctx.publish(
        tags="order.validated",
        payload={"order_id": data.order_id},
    )


bus = InMemoryBus()
bus.run(app)
```

This captures the intended first-screen mental model:

- `app.on(...)` declares behavior
- `bus.run(app)` starts the runtime
- `event.ctx.publish(...)` advances the next stage

---

22. Future Extensions

This RFC intentionally keeps v0 narrow. Future extensions may include:

- stronger dependency injection
- `ctx.request()`
- richer reply protocols
- finer metadata inheritance rules
- multi-bus cooperation
- richer snapshot / transport hints
- more subscriber implementation patterns

---

23. Conclusion

This RFC closes the v0 kernel around five layers:

1. Standard data layer
   - `Event`

2. Runtime event layer
   - `RuntimeEvent`
   - `EventContext`

3. Unified participant layer
   - `Subscriber`
   - `SubscriberResult`

4. Semantic control layer
   - `Dispatcher`
   - level propagation
   - injector fallback
   - `SessionNotConsumed`

5. Runtime and user API layer
   - `Bus`
   - `FastEvents`
   - `publish/listen/request/reply`

The goal is not maximum feature count, but minimum semantic closure.
This version is intended to be directly implementable for an in-memory first release while preserving clean boundaries for later extension.
