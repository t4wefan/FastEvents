import asyncio

from fastevents import FastEvents, InMemoryBus, RuntimeEvent


app = FastEvents()
bus = InMemoryBus()


@app.on("user.lookup")
async def lookup(event: RuntimeEvent, payload: dict) -> None:
    await event.ctx.reply(payload={"user_id": payload["user_id"], "name": "Alice"})


async def demo_listen() -> None:
    print("listen demo:")
    async with app.listen("notification.sent", level=-1) as stream:
        await bus.publish(tags="notification.sent", payload={"ok": True, "channel": "email"})
        event = await asyncio.wait_for(stream.get(), timeout=1)
        print("received:", event.payload)


async def demo_request() -> None:
    print("request demo:")
    reply = await app.request(tags="user.lookup", payload={"user_id": 7}, timeout=1)
    print("reply:", reply.payload)


async def main() -> None:
    await bus.astart(app)
    try:
        await demo_listen()
        await demo_request()
    finally:
        await bus.astop()


if __name__ == "__main__":
    asyncio.run(main())
