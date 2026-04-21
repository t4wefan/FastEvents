from __future__ import annotations

import asyncio

from pydantic import BaseModel

from fastevents import FastEvents, InMemoryBus, RuntimeEvent, dependency, rpc_context
from fastevents.ext.rpc import RpcContext, RpcExtension


app = FastEvents()
bus = InMemoryBus()
rpc = RpcExtension(app)


class ChatRequest(BaseModel):
    session_id: str
    prompt: str


class TokenChunk(BaseModel):
    session_id: str
    index: int
    text: str


class ChatReply(BaseModel):
    session_id: str
    answer: str


@dependency
def prompt_text(data: ChatRequest) -> str:
    return data.prompt.strip()


@dependency
def llm_answer(text: str = prompt_text()) -> str:
    return f"Echo: {text}. FastEvents can stream tokens and send a final rpc reply."


@app.on("chat.request")
async def run_llm(event: RuntimeEvent, data: ChatRequest, rpc: RpcContext = rpc_context(), answer: str = llm_answer()) -> None:
    for index, char in enumerate(answer):
        await event.ctx.publish(tags="chat.token", payload=TokenChunk(session_id=data.session_id, index=index, text=char))
        await asyncio.sleep(0.01)
    await event.ctx.publish(tags="chat.done", payload={"session_id": data.session_id})
    await rpc.reply(payload=ChatReply(session_id=data.session_id, answer=answer))


@app.on("chat.token")
async def render_token(data: TokenChunk) -> None:
    print(data.text, end="", flush=True)


@app.on("chat.done")
async def render_done(event: RuntimeEvent) -> None:
    print()

@app.on("chat.bye")
async def chat_bye() -> None:
    print("bye")

async def main() -> None:
    await bus.astart(app)
    try:
        print("FastEvents chat demo")
        print("type a prompt, or /quit to exit")

        session = 0
        while True:
            try:
                prompt = input("\n> ")
            except :  # noqa: E722
                await rpc.request_one("chat.bye")
                return
            if prompt.strip() in {"/quit", "/exit"}:
                await rpc.request_one("chat.bye")
                return
            session += 1
            request = ChatRequest(session_id=f"chat-{session}", prompt=prompt)
            print("stream: ", end="", flush=True)
            await rpc.request_one("chat.request", ChatReply, payload=request)
            # print(f"reply: {reply.answer}")
    except asyncio.exceptions.CancelledError:
        return


if __name__ == "__main__":
    asyncio.run(main())
