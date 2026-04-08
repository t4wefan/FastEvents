from __future__ import annotations

import asyncio

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus, RuntimeEvent, SessionNotConsumed


class UserLookup(BaseModel):
    user_id: int


async def smoke_test() -> None:
    app = FastEvents()
    bus = InMemoryBus()
    seen: list[str] = []

    @app.on("audit.user.lookup", level=-1)
    async def audit(event: RuntimeEvent) -> None:
        seen.append(f"audit:{event.tags[0]}")

    @app.on("user.lookup", level=0)
    async def typed_lookup(data: UserLookup) -> None:
        if data.user_id < 0:
            raise SessionNotConsumed()

    @app.on("user.lookup", level=1)
    async def fallback_lookup(event: RuntimeEvent, payload: dict) -> None:
        await event.ctx.reply(
            payload={
                "user_id": payload.get("user_id", -1),
                "name": "fallback",
                "path": "fallback",
            }
        )

    @app.on("user.lookup", level=0)
    async def primary_lookup(event: RuntimeEvent, data: UserLookup) -> None:
        await event.ctx.publish(tags="audit.user.lookup", payload={"user_id": data.user_id})
        await event.ctx.reply(
            payload={
                "user_id": data.user_id,
                "name": "Alice",
                "path": "primary",
            }
        )

    await bus.astart(app)
    try:
        primary = await bus.request(tags="user.lookup", payload={"user_id": 7}, timeout=1)
        fallback = await bus.request(tags="user.lookup", payload={"raw": "fallback"}, timeout=1)
    finally:
        await bus.astop()

    assert primary.payload == {"user_id": 7, "name": "Alice", "path": "primary"}
    assert fallback.payload == {"user_id": -1, "name": "fallback", "path": "fallback"}
    assert seen == ["audit:audit.user.lookup"]

    print("smoke test passed")
    print("primary reply:", primary.payload)
    print("fallback reply:", fallback.payload)
    print("observed:", seen)


def main() -> None:
    asyncio.run(smoke_test())


if __name__ == "__main__":
    main()
