from __future__ import annotations

import asyncio
import unittest

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus, RequestTimeoutError, RuntimeEvent, SessionNotConsumed
from fastevents.subscription import normalize_tags


class OrderPayload(BaseModel):
    order_id: int


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

    async def test_request_reply_roundtrip(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()

        @app.on("user.lookup")
        async def lookup(event: RuntimeEvent, payload: dict):
            await event.ctx.reply(payload={"user_id": payload["user_id"], "name": "Alice"})

        await bus.astart(app)
        try:
            reply = await app.request(tags="user.lookup", payload={"user_id": 7}, timeout=1)
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(reply.payload, {"user_id": 7, "name": "Alice"})
        self.assertIn("correlation_id", reply.meta)

    async def test_publish_accepts_explicit_reply_tags(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        seen: list[tuple[str, ...]] = []

        @app.on("user.lookup")
        async def lookup(event: RuntimeEvent, payload: dict) -> None:
            seen.append(tuple(event.meta.get("reply_tags", ())))
            await event.ctx.reply(payload={"ok": True, **payload})

        await bus.astart(app)
        try:
            async with app.listen(("reply.manual", "session.1"), level=-1) as stream:
                published = await bus.publish(
                    tags="user.lookup",
                    payload={"user_id": 7},
                    reply_tags=("reply.manual", "session.1"),
                )
                emitted = await asyncio.wait_for(stream.get(), timeout=1)
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(published.meta["reply_tags"], ("reply.manual", "session.1"))
        self.assertEqual(seen, [("reply.manual", "session.1")])
        self.assertEqual(emitted.payload, {"ok": True, "user_id": 7})

    async def test_ctx_publish_accepts_explicit_reply_tags(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()

        @app.on("start")
        async def start(event: RuntimeEvent) -> None:
            await event.ctx.publish(tags="next", payload={"step": 1}, reply_tags="reply.next")

        await bus.astart(app)
        try:
            async with app.listen("next", level=-1) as stream:
                await bus.publish(tags="start")
                emitted = await asyncio.wait_for(stream.get(), timeout=1)
            await bus.astop()
        finally:
            if bus._started:  # type: ignore[attr-defined]
                await bus.astop()

        self.assertEqual(emitted.meta["reply_tags"], ("reply.next",))

    async def test_request_timeout(self) -> None:
        app = FastEvents()
        bus = InMemoryBus()
        await bus.astart(app)
        try:
            with self.assertRaises(RequestTimeoutError):
                await app.request(tags="missing.handler", payload={}, timeout=0.01)
        finally:
            await bus.astop()

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
