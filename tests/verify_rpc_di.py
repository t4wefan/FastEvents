from __future__ import annotations

import asyncio

from pydantic import BaseModel

from fastevents import EventContext, FastEvents, InMemoryBus, RpcContext, RuntimeEvent, SessionNotConsumed, dependency, rpc_context
from fastevents.ext.rpc import RpcExtension, RpcRequestTimeoutError


class OrderPayload(BaseModel):
    order_id: int


class LookupReply(BaseModel):
    user_id: int
    name: str


async def main() -> None:
    app = FastEvents()
    rpc = RpcExtension(app)
    bus = InMemoryBus()

    timeline: list[str] = []
    dep_calls: list[int] = []

    @dependency
    def order_ctx(event: RuntimeEvent, ctx: EventContext, data: OrderPayload) -> tuple[int, bool]:
        return (data.order_id, ctx is event.ctx)

    @dependency
    def shared_counter() -> int:
        dep_calls.append(len(dep_calls) + 1)
        return dep_calls[-1]

    @app.on("order.created", level=-1)
    async def audit_orders(event: RuntimeEvent) -> None:
        timeline.append(f"audit:{event.tags[0]}")

    @app.on("order.created", level=0)
    async def primary_order(info: tuple[int, bool] = order_ctx(), counter: int = shared_counter()) -> None:
        timeline.append(f"primary:{info[0]}:{counter}")
        raise SessionNotConsumed()

    @app.on("order.created", level=1)
    async def fallback_order(counter: int = shared_counter()) -> None:
        timeline.append(f"fallback:{counter}")

    @app.on("user.lookup")
    async def lookup(payload: dict, rpc = rpc_context()) -> None:
        if payload.get("mode") == "many":
            await rpc.reply(payload={"user_id": 1, "name": "Alice"})
            await rpc.reply(payload={"user_id": 2, "name": "Bob"})
            return
        await rpc.reply(payload={"user_id": payload["user_id"], "name": "Alice"})

    @app.on("order.created.fallback")
    async def fallback_projection(event: RuntimeEvent) -> None:
        timeline.append(f"projection:{event.tags[0]}")

    await bus.astart(app)
    try:
        await app.publish(tags="order.created", payload={"order_id": 7})

        async with app.listen("order.created", level=-1) as stream:
            await app.publish(tags="order.created", payload={"order_id": 8})
            observed = await stream.get()
            timeline.append(f"listen:{observed.payload['order_id']}")

        many = await rpc.request(tags="user.lookup", payload={"mode": "many"}, max_size=2)
        one = await rpc.request_one("user.lookup", LookupReply, payload={"user_id": 9})

        stream = await rpc.request_stream(tags="user.lookup", payload={"mode": "many"}, max_size=2)
        try:
            stream_results = [reply async for reply in stream]
        finally:
            await stream.close()

        timeout_raised = False
        try:
            await rpc.request_one(tags="missing.handler", payload={}, timeout=0.01)
        except RpcRequestTimeoutError:
            timeout_raised = True

        assert timeline.count("audit:order.created") == 2, timeline
        assert "listen:7" in timeline or "listen:8" in timeline, timeline
        assert "primary:7:1" in timeline, timeline
        assert "primary:8:2" in timeline, timeline
        assert "fallback:1" in timeline, timeline
        assert "fallback:2" in timeline, timeline
        assert dep_calls == [1, 2], dep_calls
        assert [item.payload for item in many] == [
            {"user_id": 1, "name": "Alice"},
            {"user_id": 2, "name": "Bob"},
        ], many
        assert one.user_id == 9 and one.name == "Alice", one
        assert [item.payload for item in stream_results] == [
            {"user_id": 1, "name": "Alice"},
            {"user_id": 2, "name": "Bob"},
        ], stream_results
        assert timeout_raised, "expected rpc timeout to be raised"

        print("verify_rpc_di: ok")
        print("timeline:")
        for item in timeline:
            print(f"  - {item}")
        print("note: same-level subscribers run concurrently, so timeline order is intentionally non-deterministic")
        print("rpc.request ->", [item.payload for item in many])
        print("rpc.request_one ->", one.model_dump())
        print("rpc.request_stream ->", [item.payload for item in stream_results])
    finally:
        await bus.astop()


if __name__ == "__main__":
    asyncio.run(main())
