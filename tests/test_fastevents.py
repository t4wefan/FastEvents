from __future__ import annotations

import asyncio
import contextlib
import io
import threading
import unittest
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from fastevents import EventContext, EventModel, FastEvents, InMemoryBus, RpcContext, RuntimeEvent, SessionNotConsumed, dependency, rpc_context
from fastevents.events import RuntimeEventView, new_event
from fastevents.ext.rpc import RpcExtension, RpcReplyNotAvailableError, RpcRequestTimeoutError
from fastevents.subscribers import HandlerSubscriber
from fastevents.subscription import normalize_tags


class OrderPayload(EventModel):
    order_id: int


class LookupReply(EventModel):
    user_id: int
    name: str


class FallbackPayload(EventModel):
    raw: str


class EnvelopeModel(EventModel):
    order_id: int
    note: str


class LookupRequest(BaseModel):
    user_id: int | None = None
    ok: bool | None = None
    mode: str | None = None


class CorrelationId:
    def __init__(self, value: str) -> None:
        self.value = value

    @staticmethod
    def _provider():
        @dependency
        def provider(event: RuntimeEvent) -> CorrelationId:
            return CorrelationId(str(event.meta["cid"]))

        return provider


class BrokenDependency:
    @staticmethod
    def _provider():
        @dependency
        def provider() -> BrokenDependency:
            raise RuntimeError("boom")

        return provider


@dataclass
class AuditEnvelope:
    order_id: int
    note: str


class FastEventsTests(unittest.IsolatedAsyncioTestCase):
    async def test_tag_normalization(self) -> None:
        self.assertEqual(normalize_tags(["Order.Created", "order.created"]), ("order.created",))

    async def test_same_level_subscribers_all_run(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[str] = []

        @app.on("order.created", level=0)
        async def first(event: RuntimeEvent):
            seen.append(f"first:{event.id}")

        @app.on("order.created", level=0)
        async def second(event: RuntimeEvent):
            seen.append(f"second:{event.id}")

        await bus.astart(app)
        try:
            await bus.publish(tags="order.created", payload={})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(len(seen), 2)
        self.assertEqual({item.split(":")[0] for item in seen}, {"first", "second"})

    async def test_session_not_consumed_allows_fallback(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[str] = []

        @app.on("order.created", level=0)
        async def primary(event: RuntimeEvent):
            seen.append("primary")
            raise SessionNotConsumed()

        @app.on("order.created", level=1)
        async def fallback(event: RuntimeEvent):
            seen.append("fallback")

        await bus.astart(app)
        try:
            await bus.publish(tags="order.created", payload={})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, ["primary", "fallback"])

    async def test_validation_failure_allows_payload_fallback(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[str] = []

        @app.on("order.created", level=0)
        async def model_handler(data: OrderPayload):
            seen.append(f"model:{data.order_id}")

        @app.on("order.created", level=1)
        async def fallback(payload: FallbackPayload):
            seen.append(f"fallback:{payload.raw}")

        await bus.astart(app)
        try:
            await bus.publish(tags="order.created", payload={"raw": "bad"})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, ["fallback:bad"])

    async def test_app_publish_uses_runtime_bus(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[dict] = []

        @app.on("user.lookup")
        async def lookup(payload: LookupRequest) -> None:
            seen.append(payload.model_dump(exclude_none=True))

        await bus.astart(app)
        try:
            await app.publish(tags="user.lookup", payload={"user_id": 7})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, [{"user_id": 7}])

    async def test_ctx_publish_emits_followup_event(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()

        @app.on("start")
        async def start(event: RuntimeEvent) -> None:
            await event.ctx.publish(tags="next", payload={"step": 1})

        await bus.astart(app)
        try:
            async with app.listen("next", level=-1) as stream:
                await bus.publish(tags="start")
                emitted = await asyncio.wait_for(stream.get(), timeout=1)
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(emitted.payload, {"step": 1})

    async def test_dependency_can_inject_event_context_and_payload_model(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[tuple[int, bool]] = []

        @dependency
        def order_ctx(event: RuntimeEvent, ctx: EventContext, data: OrderPayload) -> tuple[int, bool]:
            return (data.order_id, ctx is event.ctx)

        @app.on("order.created")
        async def handle(info: Any = order_ctx()) -> None:
            seen.append(info)

        await bus.astart(app)
        try:
            await app.publish(tags="order.created", payload={"order_id": 7})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, [(7, True)])

    async def test_custom_type_provider_can_inject_annotation(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[str] = []

        @app.on("provider.test")
        async def handle(cid: CorrelationId) -> None:
            seen.append(cid.value)

        await bus.astart(app)
        try:
            await app.publish(tags="provider.test", payload={}, meta={"cid": "abc-123"})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, ["abc-123"])

    async def test_dependency_result_is_cached_per_handler_call(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        calls: list[int] = []
        seen: list[tuple[int, int]] = []

        @dependency
        def sequence() -> int:
            calls.append(len(calls) + 1)
            return calls[-1]

        @app.on("cache.test")
        async def handle(first: Any = sequence(), second: Any = sequence()) -> None:
            seen.append((first, second))

        await bus.astart(app)
        try:
            await app.publish(tags="cache.test", payload={})
            await app.publish(tags="cache.test", payload={})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, [(1, 1), (2, 2)])

    async def test_dependency_cache_is_shared_across_subscribers_for_one_event(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        calls: list[int] = []
        seen: list[tuple[str, int]] = []

        @dependency
        def sequence() -> int:
            calls.append(len(calls) + 1)
            return calls[-1]

        @app.on("shared.cache")
        async def first(value: Any = sequence()) -> None:
            seen.append(("first", value))

        @app.on("shared.cache")
        async def second(value: Any = sequence()) -> None:
            seen.append(("second", value))

        await bus.astart(app)
        try:
            await app.publish(tags="shared.cache", payload={})
            await app.publish(tags="shared.cache", payload={})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, [("first", 1), ("second", 1), ("first", 2), ("second", 2)])

    async def test_dependency_cycle_is_reported(self) -> None:
        @dependency
        def dep_a(value: Any = None) -> str:
            return f"a:{value}"

        @dependency
        def dep_b(value: Any = None) -> str:
            return f"b:{value}"

        dep_a.callback.__defaults__ = (dep_b(),)
        dep_b.callback.__defaults__ = (dep_a(),)

        async def handle(value: Any = dep_a()) -> None:
            _ = value

        subscriber = HandlerSubscriber(
            id="cycle-test",
            callback=handle,
            subscription="cycle.test",
        )
        event = new_event(tags="cycle.test", payload={})
        runtime_event = RuntimeEventView(event, EventContext(InMemoryBus(), event))

        result = await subscriber.handle(runtime_event)

        self.assertFalse(result.consumed)
        self.assertIsNone(result.exc)

    async def test_injection_error_is_absorbed_and_allows_fallback(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[str] = []

        @app.on("broken.provider", level=0)
        async def primary(dep: BrokenDependency) -> None:
            seen.append("primary")

        @app.on("broken.provider", level=1)
        async def fallback(payload: OrderPayload) -> None:
            seen.append(f"fallback:{payload.order_id}")

        await bus.astart(app)
        try:
            await app.publish(tags="broken.provider", payload={"order_id": 9})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, ["fallback:9"])

    async def test_debug_logs_flow_dispatch_and_absorbed_injection_errors(self) -> None:
        app = FastEvents(debug=True)
        bus = InMemoryBus()
        seen: list[str] = []

        @app.on("debug.trace", level=0)
        async def primary(dep: BrokenDependency) -> None:
            seen.append("primary")

        @app.on("debug.trace", level=1)
        async def fallback(payload: OrderPayload) -> None:
            seen.append(f"fallback:{payload.order_id}")

        output = io.StringIO()
        await bus.astart(app)
        try:
            with contextlib.redirect_stdout(output):
                await app.publish(tags="debug.trace", payload={"order_id": 3})
                await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        debug_output = output.getvalue()
        self.assertEqual(seen, ["fallback:3"])
        self.assertIn("outgoing", debug_output)
        self.assertIn("incoming", debug_output)
        self.assertIn("dispatch level=0", debug_output)
        self.assertIn("absorbed injection error", debug_output)

    async def test_tuple_payload_round_trip_preserves_tuple_marker(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()

        await bus.astart(app)
        try:
            async with app.listen("tuple.payload", level=-1) as stream:
                await app.publish(tags="tuple.payload", payload=(1, "two", True))
                event = await asyncio.wait_for(stream.get(), timeout=1)
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(event.payload, (1, "two", True))

    async def test_dataclass_payload_is_json_compatible_on_receive(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[dict[str, Any]] = []

        @app.on("dataclass.payload")
        async def handle(data: EnvelopeModel) -> None:
            seen.append(data.model_dump())

        await bus.astart(app)
        try:
            await app.publish(tags="dataclass.payload", payload=AuditEnvelope(order_id=7, note="ok"))
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, [{"order_id": 7, "note": "ok"}])

    async def test_bytes_payload_can_be_injected_directly(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[bytes] = []

        @app.on("bytes.payload")
        async def handle(payload: bytes) -> None:
            seen.append(payload)

        await bus.astart(app)
        try:
            await app.publish(tags="bytes.payload", payload=b"hello")
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, [b"hello"])

    async def test_dict_payload_can_be_injected_directly(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[dict[str, Any]] = []

        @app.on("dict.payload")
        async def handle(payload: dict) -> None:
            seen.append(payload)

        await bus.astart(app)
        try:
            await app.publish(tags="dict.payload", payload={"a": 1, "b": True})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(seen, [{"a": 1, "b": True}])


class FastEventsSyncBusTests(unittest.TestCase):
    def test_sync_start_owns_background_loop_and_sync_publish_works(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[dict[str, Any]] = []
        received = threading.Event()

        @app.on("sync.publish")
        async def handle(payload: dict) -> None:
            seen.append(payload)
            received.set()

        bus.start(app)
        try:
            bus.sync_publish(tags="sync.publish", payload={"ok": True})
            self.assertTrue(received.wait(1))
        finally:
            bus.stop()

        self.assertEqual(seen, [{"ok": True}])

    def test_sync_send_accepts_prebuilt_event(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[bytes] = []
        received = threading.Event()

        @app.on("sync.send")
        async def handle(payload: bytes) -> None:
            seen.append(payload)
            received.set()

        event = new_event(tags="sync.send", payload=b"payload")

        bus.start(app)
        try:
            bus.sync_send(event)
            self.assertTrue(received.wait(1))
        finally:
            bus.stop()

        self.assertEqual(seen, [b"payload"])
