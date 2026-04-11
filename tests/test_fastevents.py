from __future__ import annotations

import asyncio
import unittest
from typing import Any

from pydantic import BaseModel

from fastevents import EventContext, FastEvents, InMemoryBus, RpcContext, RuntimeEvent, SessionNotConsumed, dependency, rpc_context
from fastevents.events import RuntimeEventView, new_event
from fastevents.exceptions import InjectionError
from fastevents.ext.rpc import RpcReplyNotAvailableError, RpcRequestTimeoutError
from fastevents.subscribers import HandlerSubscriber
from fastevents.subscription import normalize_tags


class OrderPayload(BaseModel):
    order_id: int


class LookupReply(BaseModel):
    user_id: int
    name: str


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
        async def fallback(payload: dict):
            seen.append(f"fallback:{payload['raw']}")

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
        async def lookup(payload: dict) -> None:
            seen.append(payload)

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

        self.assertTrue(result.consumed)
        self.assertIsInstance(result.exc, InjectionError)

    async def test_rpc_request_returns_all_replies(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()

        @app.on("user.lookup")
        async def lookup(rpc: Any = rpc_context()) -> None:
            await rpc.reply(payload={"user_id": 1, "name": "Alice"})
            await rpc.reply(payload={"user_id": 2, "name": "Bob"})

        await bus.astart(app)
        try:
            rpc = getattr(app.ex, "rpc")
            replies = await rpc.request(tags="user.lookup", payload={"ok": True}, max_size=2)
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual([reply.payload for reply in replies], [{"user_id": 1, "name": "Alice"}, {"user_id": 2, "name": "Bob"}])

    async def test_rpc_request_one_returns_first_reply(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()

        @app.on("user.lookup")
        async def lookup(rpc: Any = rpc_context()) -> None:
            await rpc.reply(payload={"user_id": 7, "name": "Alice"})
            await rpc.reply(payload={"user_id": 8, "name": "Bob"})

        await bus.astart(app)
        try:
            rpc = getattr(app.ex, "rpc")
            reply = await rpc.request_one(tags="user.lookup", payload={"ok": True})
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(reply.payload, {"user_id": 7, "name": "Alice"})

    async def test_rpc_request_stream_yields_replies(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()

        @app.on("user.lookup")
        async def lookup(rpc: Any = rpc_context()) -> None:
            await rpc.reply(payload={"user_id": 1, "name": "Alice"})
            await rpc.reply(payload={"user_id": 2, "name": "Bob"})

        await bus.astart(app)
        try:
            rpc = getattr(app.ex, "rpc")
            stream = await rpc.request_stream(tags="user.lookup", payload={"ok": True}, max_size=2)
            try:
                replies = [reply async for reply in stream]
            finally:
                await stream.close()
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual([reply.payload for reply in replies], [{"user_id": 1, "name": "Alice"}, {"user_id": 2, "name": "Bob"}])

    async def test_rpc_request_timeout_raises(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()

        await bus.astart(app)
        try:
            rpc = getattr(app.ex, "rpc")
            with self.assertRaises(RpcRequestTimeoutError):
                await rpc.request_one(tags="missing.handler", payload={}, timeout=0.01)
        finally:
            await bus.astop()

    async def test_rpc_request_can_validate_model(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()

        @app.on("user.lookup")
        async def lookup(rpc: Any = rpc_context()) -> None:
            await rpc.reply(payload={"user_id": 7, "name": "Alice"})

        await bus.astart(app)
        try:
            rpc = getattr(app.ex, "rpc")
            reply = await rpc.request_one(tags="user.lookup", payload={"ok": True}, model=LookupReply)
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(reply.user_id, 7)
        self.assertEqual(reply.name, "Alice")

    async def test_rpc_context_reply_requires_reply_tags(self) -> None:
        event = new_event(tags="x", payload={})
        runtime_event = RuntimeEventView(event, EventContext(InMemoryBus(), event))
        rpc = RpcContext(event=runtime_event, ctx=runtime_event.ctx)

        with self.assertRaises(RpcReplyNotAvailableError):
            await rpc.reply(payload={"ok": True})

    async def test_app_listen_exposes_get_helper(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()

        await bus.astart(app)
        try:
            async with app.listen("notification.sent", level=-1) as stream:
                await bus.publish(tags="notification.sent", payload={"ok": True})
                event = await asyncio.wait_for(stream.get(), timeout=1)
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(event.payload, {"ok": True})
