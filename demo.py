from __future__ import annotations

import asyncio
import sys

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus, RuntimeEvent


app = FastEvents()
bus = InMemoryBus()


class ChatTurn(BaseModel):
    session_id: str
    prompt: str

class ChatDone(BaseModel):
    session_id: str

class Token(BaseModel):
    session_id: str
    token:str


def line(text: str = "") -> None:
    print(text)


@app.on("chat.request", level=0, name="mock-llm")
async def mock_llm(event: RuntimeEvent, data: ChatTurn) -> None:
    text = (
        f"You said: {data.prompt}. "
        "This answer is generated as a stream of terminal events. "
        "Each chunk becomes its own event before it is rendered.\n"
    )
    for chunk in str(text):
        await event.ctx.publish(
            tags="chat.token",
            payload=Token(session_id=data.session_id,token=chunk),
        )
        await asyncio.sleep(0.001)
    await event.ctx.publish(tags="chat.done", payload=ChatDone(session_id=data.session_id))


@app.on("chat.token", level=0, name="terminal-renderer")
async def render_token(event: RuntimeEvent, payload: Token) -> None:
    print(payload.token,flush=True,end="")


async def run_prompt(session_id: str, prompt: str) -> None:
    line(f"\n> user: {prompt}")
    async with bus.listen("chat.done", level=-1) as stream:
        await bus.publish(tags="chat.request", payload=ChatTurn(session_id=session_id,prompt=prompt))
        async for e in stream:
            payload = ChatDone.model_validate(e.payload)
            print(f'{payload.session_id} is finished')
            break


async def interactive_demo() -> None:
    await bus.astart(app)
    line("simple streaming terminal demo")
    line("type a message, or /quit to exit")
    counter = 0
    try:
        while True:
            prompt = await asyncio.to_thread(input, "\n> ")
            if prompt.strip() in {"/quit", "/exit"}:
                line("bye")
                return
            counter += 1
            await run_prompt(session_id=f"interactive-{counter}", prompt=prompt)
    except asyncio.exceptions.CancelledError:
        line("bye")
        return
        
        
if __name__ == "__main__":
    try:
        asyncio.run(interactive_demo())
    except KeyboardInterrupt:
        exit()
