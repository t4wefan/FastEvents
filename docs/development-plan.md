# FastEvents Development Plan

Status: Draft

This document is an internal development guide for the next stage of FastEvents.
It is not an external RFC and not a compatibility promise. Its purpose is to
define what to keep, what to remove, what to build next, and which design
guardrails must remain stable during implementation.

## 1. Purpose

The current project already has a workable core:

- standard events
- tag-based subscription DSL
- level-based propagation
- dispatcher-driven runtime semantics
- in-memory bus
- minimal handler injection

The next stage is not feature expansion. The next stage is boundary reduction.

We want to:

- keep the core small and stable
- remove APIs that do not belong to the core model
- move higher-level modes out of the core surface
- create a clean path for extensions
- prepare the codebase for DI v1 and future bridge/distributed work

## 2. Development Goals

### 2.1 Primary goals

- keep `event`, `tags DSL`, `level`, `dispatcher`, `bus`, minimal `ctx`, and
  lightweight DI as the core model
- make `app` the only external capability boundary
- ensure extensions depend on `app`, never directly on `bus`
- remove non-core APIs instead of preserving them for compatibility
- add the minimum structure needed for future extensions

### 2.2 Non-goals for this stage

- no distributed bus implementation yet
- no bridge implementation yet
- no full plugin lifecycle framework
- no heavy DI container or provider registry
- no compatibility layer for APIs that have been judged non-core

## 3. Stable Design Direction

### 3.1 Core boundary

The core should converge around the following concepts:

- `StandardEvent`
- `RuntimeEvent`
- `EventContext`
- subscription DSL
- level propagation
- `Dispatcher`
- `Bus`
- `FastEvents`
- lightweight dependency resolution

### 3.2 App is the external boundary

`app` is the only public coordination boundary.

This means:

- extensions can access `app`
- extensions cannot access `bus` directly
- runtime edge cases are handled by `app`
- app-level APIs may delegate to `bus`, but they remain app-owned capability
  surfaces

### 3.3 App-level publish capability is intentional

To preserve isolation, `app` needs its own message publishing capability.

This is not a second runtime mechanism.
It is a controlled app-facing boundary that delegates to the active runtime.

The role split is:

- `bus.publish(...)`: runtime-side event admission and delivery capability
- `app.publish(...)`: app-side controlled sending capability for extensions and
  upper-layer code

`app.publish(...)` may look similar to `bus.publish(...)`, but the duplication is
architectural, not semantic. It exists to preserve the rule that extension code
depends on `app`, not on `bus`.

### 3.4 Extensions are app-bound

Extension composition should stay explicit instead of using a mount namespace.

It is not:

- a second runtime system
- a plugin lifecycle container
- a place to expose `bus`

Extensions receive `app` and use app-exposed capabilities.
If an extension needs extra initialization or resource handling, that logic is
internal to the extension and not part of a core lifecycle contract.

## 4. Direct Removals

This stage explicitly allows removal of non-core APIs.
If an API does not belong to the core model, it should be removed rather than
kept for compatibility.

### 4.1 APIs to remove from the core

- `FastEvents.request(...)`
- `Bus.request(...)`
- `InMemoryBus.request(...)`
- `EventContext.reply(...)`

### 4.2 Semantics to remove from the core path

- reply-specific metadata handling as a core concern
- request/reply as a core framework story
- core tests and README sections that present rpc semantics as built-in

### 4.3 Replacement direction

If request/reply remains valuable, it should return later as an extension-level
pattern, not as a core API.

Likely future form:

- `rpc.request(...)`
- `RpcContext.reply(...)`

## 5. Core Areas To Keep Stable

### 5.1 Dispatcher semantics

`Dispatcher` remains a protected core area.

Do not change its semantics to satisfy extension needs.
The following rules should remain stable:

- subscriber matching is tag-driven
- propagation is ordered by ascending `level`
- same-level subscribers run concurrently
- negative levels are observation-only
- non-negative levels stop upward propagation when consumed

### 5.2 Event model

The event model should remain split into:

- standard event data
- runtime event view with `ctx`

Standard events remain data-first objects.
Runtime capabilities remain explicit.

### 5.3 Tag normalization and subscription DSL

Do not expand the DSL for unrelated high-level modes.
The DSL already carries a large amount of expressive power.

### 5.4 Minimal context philosophy

`ctx` should remain small.

The long-term goal is for `ctx` to expose minimal runtime capability, not every
high-level protocol helper.

## 6. New Capabilities Needed

### 6.1 App publish capability

Add an app-level publishing API.

Proposed direction:

```python
await app.publish(tags="task.submit", payload={"task_id": "t-1"})
```

Requirements:

- fail clearly if no runtime is available
- internally delegate to the active runtime bus
- expose the same standard-event entry semantics expected by extensions

This API is needed before extension work, because extensions must not call bus
methods directly.

### 6.2 DI v1

The current signature-based injection should evolve into a minimal dependency
system.

Required capabilities:

- `@dependency`
- recursive dependency resolution
- per-subscriber-call caching
- cycle detection
- explicit failure semantics

Current baseline:

- function-style dependencies are available
- dependencies may consume `RuntimeEvent`, `EventContext`, payload models, and
  other dependencies
- results are cached per event dispatch
- dependency cycles raise `InjectionError`

Minimum built-in injectable sources:

- `RuntimeEvent`
- payload model
- minimal runtime context
- other dependencies

### 6.3 Explicit extension composition

Keep extension composition explicit.

Requirements:

- extensions are constructed explicitly with `app`
- extensions receive `app`
- extensions cannot directly access bus internals

### 6.4 Future sample extension target

After DI v1 stabilizes, implement one official sample extension.

Preferred target:

- rpc extension

Reason:

- it proves that removed request/reply semantics can live outside the core
- it validates DI-provided typed context
- it validates app-bound extension design

## 7. Current Codebase Impact

### 7.1 `fastevents/app.py`

This file will become the main focus of the public boundary redesign.

Changes needed:

- add `app.publish(...)`
- keep runtime binding internal
- remove `request(...)`
- decide whether `listen(...)` remains core; current recommendation is to keep
  it for now as an event-stream observation capability

Design note:

`app` may delegate to the runtime, but must remain the place where boundary
violations are checked and normalized.

### 7.2 `fastevents/bus.py`

This file should be narrowed to runtime concerns.

Changes needed:

- remove `request(...)` from the abstract bus contract
- remove `request(...)` from `InMemoryBus`
- keep `publish(...)`, `send(...)`, `ingest(...)`, `sync(...)`, and lifecycle
  APIs focused on runtime delivery
- avoid adding extension-oriented helpers here

### 7.3 `fastevents/events.py`

This file defines the runtime capability surface.

Changes needed:

- keep `EventContext.publish(...)`
- remove `EventContext.reply(...)`
- keep event construction free from rpc-specific core semantics

Design note:

If future extension contexts need rich protocol operations, they should come
from DI-resolved typed contexts, not from growing `EventContext`.

### 7.4 `fastevents/subscribers.py`

This file is the best starting point for DI v1.

Changes needed:

- separate signature parsing from execution-time dependency resolution
- introduce dependency graph resolution
- add per-call cache
- add cycle detection
- preserve simple event/payload injection as the minimum supported path

This is likely the highest-value implementation area after API removal.

### 7.5 `fastevents/dispatcher.py`

This file should remain semantically stable.

Changes should be minimal and only in service of DI/runtime plumbing that does
not alter propagation rules.

### 7.6 `fastevents/__init__.py`

This file will need cleanup after API removals and later extension additions.

Expected changes:

- remove exports tied to removed core rpc APIs
- add future exports only if they belong to the true core surface

### 7.7 `tests/test_fastevents.py`

This file should be treated as both regression protection and redesign work.

Changes needed:

- remove tests for deleted request/reply core APIs
- preserve tests for dispatcher semantics and base publishing behavior
- add tests for `app.publish(...)`
- add tests for DI v1 behavior
- later add tests for extension mounting

### 7.8 Documentation

Affected documentation files:

- `README.md`
- `README.zh-CN.md`
- `docs/rfc.md`

Needed updates:

- stop presenting request/reply as a core capability
- describe the smaller core boundary
- document `app.publish(...)` once implemented
- eventually replace old RFC assumptions with the new direction

## 8. Phased Implementation Plan

### Phase 1: Core surface reduction

Goal:

- remove non-core APIs from the codebase
- update tests and docs to match

Tasks:

- delete `request(...)` from app and bus
- delete `reply(...)` from `EventContext`
- remove rpc-oriented tests
- update READMEs and developer docs

Completion signal:

- the core no longer exposes request/reply as built-in capability

### Phase 2: App boundary strengthening

Goal:

- make `app` a real external capability boundary

Tasks:

- add `app.publish(...)`
- define app-side runtime availability behavior
- ensure extensions can rely on app-side send capability without knowing bus

Completion signal:

- upper-layer code can publish through `app`
- no new design pressure exists to expose `bus` directly to extensions

### Phase 3: DI v1

Goal:

- replace special-case handler injection with a minimal dependency model

Tasks:

- add dependency declaration form
- resolve nested dependencies
- cache per subscriber call
- detect cycles
- define error behavior

Completion signal:

- typed runtime helper objects can be provided through DI without growing core
  ctx

### Phase 4: Extension mount surface

Goal:

- allow clean app-bound extension composition

Tasks:

- define explicit extension construction behavior
- ensure extensions receive app only

Completion signal:

- extensions can be composed without runtime leakage

### Phase 5: Official sample extension

Goal:

- prove the architecture by rebuilding one removed capability outside the core

Tasks:

- implement rpc as an extension
- expose app-side API through `rpc`
- expose handler-side API through a DI-resolved typed context

Completion signal:

- request/reply works as an extension without re-expanding core boundaries

## 9. Design Guardrails

These rules should be treated as hard constraints during implementation.

### 9.1 Do not re-expand the core for removed modes

If a mode has been judged non-core, do not reintroduce it into app, bus, ctx,
or dispatcher under a different name.

### 9.2 Do not let extensions depend on bus

No direct bus reference should become the normal programming model for
extensions.

### 9.3 Do not turn extension composition into a lifecycle framework

Explicit extension composition is not a plugin manager.

### 9.4 Do not grow `ctx` into a universal surface

Use DI-resolved typed contexts for richer capabilities.

### 9.5 Do not add dispatcher special cases for extensions

Extension requirements must be expressed through existing core mechanics or
through app/DI composition.

### 9.6 Do not introduce a heavy DI container

Dependencies remain function-based and local in spirit.

### 9.7 Meta is not a core semantic surface in this stage

At this stage, core propagation does not depend on `meta`.
`Bus` and `Dispatcher` should not begin interpreting `meta` for extension-
specific behavior.
If future work requires core-level metadata semantics, they should be
introduced explicitly by a later design decision, not implicitly during
feature work.

## 10. Acceptance Criteria

### 10.1 After Phase 1

- no core request/reply APIs remain
- docs no longer describe rpc as built-in
- dispatcher and publish semantics still pass tests

### 10.2 After Phase 2

- `app.publish(...)` exists and behaves as the public sending boundary
- app-owned sending works without exposing bus directly

### 10.3 After Phase 3

- dependency resolution supports nesting, cache, and cycle detection
- typed helper contexts can be injected without expanding core ctx

### 10.4 After Phase 4

- extensions receive app
- extensions do not rely on direct bus access

### 10.5 After Phase 5

- rpc-style request/reply exists as an extension
- the core remains smaller than before rpc removal

## 11. Immediate Next Steps

The next implementation steps should be taken in this order:

1. remove core request/reply APIs
2. add `app.publish(...)`
3. update docs to reflect the new boundary
4. refactor injection toward DI v1
5. keep extension construction explicit
6. rebuild rpc as an extension sample

This order matters.
Without app-side publish, extension isolation is incomplete.
Without DI v1, typed extension contexts have no proper landing point.
Without both, explicit extension composition would remain only a guideline and not a usable extension surface.
